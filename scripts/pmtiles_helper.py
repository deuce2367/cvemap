#!/usr/bin/env python3
"""
Resilient, Resumable & Verifying PMTiles Download Helper for CVEMAP.

Solves three failure modes in large remote `go-pmtiles extract` runs:
1. Prevents Cloudflare / CDN HTTP/2 `INTERNAL_ERROR` stream resets on multi-GB
   extractions by routing `go-pmtiles` through a local HTTP/1.1 range-slicing
   proxy (32 MiB chunks, persistent keep-alive, 12x exponential-backoff retry
   resuming at the exact byte offset).
2. Stages downloads into `<output>.part` + `<output>.part.json` and supports
   true cross-run byte-level resume (including in-place repair of interrupted,
   sparse/headerless full-planet extracts from `go-pmtiles`).
3. Performs deep post-download & standalone (`--verify`) integrity checks:
   header bounds, Linux `SEEK_HOLE` sparse-hole scan across the entire file,
   sample first/last tile gzip decompression, and `pmtiles verify`.
"""

import argparse
import gzip
import http.client
import http.server
import json
import math
import os
import shutil
import socketserver
import ssl
import struct
import subprocess
import sys
import threading
import time
import urllib.parse
import zlib

HEADER_FMT = "<7sB11Q6B4iB2i"
HEADER_SIZE = 127
CHUNK_BYTES = 32 * 1024 * 1024        # 32 MiB upstream HTTP/1.1 range window
STREAM_BUF = 256 * 1024               # 256 KiB socket copy buffer
REWIND_SAFETY_BYTES = 1024 * 1024     # 1 MiB rewind before SEEK_HOLE on resume
USER_AGENT = "go-pmtiles/1.28 (CVEMAP-Resumable/1.0)"


def fmt_bytes(n: float) -> str:
    for unit in ("B", "KiB", "MiB", "GiB", "TiB"):
        if abs(n) < 1024.0 or unit == "TiB":
            return f"{n:.2f} {unit}" if unit != "B" else f"{int(n)} B"
        n /= 1024.0
    return f"{n:.2f} TiB"


def fmt_duration(sec: float) -> str:
    if sec < 0 or math.isinf(sec) or math.isnan(sec):
        return "--:--"
    s = int(round(sec))
    h, rem = divmod(s, 3600)
    m, s = divmod(rem, 60)
    return f"{h:d}h{m:02d}m{s:02d}s" if h > 0 else f"{m:02d}m{s:02d}s"


# ==============================================================================
# Resilient HTTP/1.1 Range Client (Zero HTTP/2, Auto-Retry & Redirect Support)
# ==============================================================================


class ResilientRangeFetcher:
    def __init__(self, url: str, max_retries: int = 12, timeout: float = 30.0):
        self.initial_url = url
        self.resolved_url = url
        self.max_retries = max_retries
        self.timeout = timeout
        self._ssl_ctx = ssl.create_default_context()
        self._total_size: int | None = None
        self._lock = threading.Lock()

    def _open_conn(self, parsed: urllib.parse.SplitResult) -> http.client.HTTPConnection:
        port = parsed.port
        if parsed.scheme == "https":
            return http.client.HTTPSConnection(
                parsed.hostname, port or 443, timeout=self.timeout, context=self._ssl_ctx
            )
        return http.client.HTTPConnection(parsed.hostname, port or 80, timeout=self.timeout)

    def get_total_size(self) -> int:
        with self._lock:
            if self._total_size is not None:
                return self._total_size
        data, total = self.fetch_bytes(0, 0)
        if total is None or len(data) != 1:
            raise RuntimeError(f"Remote server does not support HTTP Range requests: {self.initial_url}")
        with self._lock:
            self._total_size = total
        return total

    def fetch_bytes(self, start: int, end: int) -> tuple[bytes, int | None]:
        out = bytearray()
        total_ref: list[int | None] = [None]

        def writer(chunk: bytes, total: int | None) -> None:
            if total is not None:
                total_ref[0] = total
            out.extend(chunk)

        self.stream_range(start, end, writer)
        return bytes(out), total_ref[0]

    def stream_range(self, start: int, end: int, on_chunk) -> None:
        """Stream inclusive byte range [start..end] in <=32 MiB HTTP/1.1 windows with retry."""
        pos = start
        while pos <= end:
            win_end = min(end, pos + CHUNK_BYTES - 1)
            attempt = 0
            while True:
                conn = None
                try:
                    cur_url = self.resolved_url
                    redirects = 0
                    while True:
                        parsed = urllib.parse.urlsplit(cur_url)
                        path_q = parsed.path or "/"
                        if parsed.query:
                            path_q += "?" + parsed.query
                        conn = self._open_conn(parsed)
                        headers = {
                            "User-Agent": USER_AGENT,
                            "Range": f"bytes={pos}-{win_end}",
                            "Accept-Encoding": "identity",
                            "Connection": "close",
                        }
                        conn.request("GET", path_q, headers=headers)
                        resp = conn.getresponse()

                        if resp.status in (301, 302, 303, 307, 308):
                            loc = resp.getheader("Location")
                            resp.read()
                            conn.close()
                            conn = None
                            if not loc:
                                raise RuntimeError(f"Redirect HTTP {resp.status} without Location")
                            cur_url = urllib.parse.urljoin(cur_url, loc)
                            redirects += 1
                            if redirects > 6:
                                raise RuntimeError("Too many HTTP redirects")
                            if resp.status in (301, 308):
                                self.resolved_url = cur_url
                            continue
                        break

                    if resp.status not in (200, 206):
                        body_preview = resp.read(256).decode("utf-8", errors="ignore")
                        raise RuntimeError(f"HTTP {resp.status} {resp.reason}: {body_preview}")

                    total_size = None
                    cr = resp.getheader("Content-Range", "")
                    if "/" in cr:
                        tail = cr.split("/")[-1].strip()
                        if tail.isdigit():
                            total_size = int(tail)
                            with self._lock:
                                self._total_size = total_size

                    expected = win_end - pos + 1
                    received = 0
                    while received < expected:
                        buf = resp.read(min(STREAM_BUF, expected - received))
                        if not buf:
                            raise EOFError(
                                f"Premature EOF after {received}/{expected} bytes at offset {pos + received}"
                            )
                        on_chunk(buf, total_size)
                        pos += len(buf)
                        received += len(buf)

                    conn.close()
                    break
                except KeyboardInterrupt:
                    if conn:
                        conn.close()
                    raise
                except Exception as exc:
                    if conn:
                        try:
                            conn.close()
                        except Exception:
                            pass
                    attempt += 1
                    if attempt > self.max_retries:
                        raise RuntimeError(
                            f"Failed fetching bytes {pos}-{win_end} after {self.max_retries} retries: {exc}"
                        ) from exc
                    delay = min(30.0, 1.5 * (1.6 ** (attempt - 1)))
                    sys.stderr.write(
                        f"\n[retry {attempt}/{self.max_retries}] Transient stream error at {fmt_bytes(pos)} "
                        f"({exc}); reconnecting in {delay:.1f}s ...\n"
                    )
                    sys.stderr.flush()
                    time.sleep(delay)


# ==============================================================================
# Local Loopback HTTP/1.1 Range Proxy for go-pmtiles
# ==============================================================================


class _ThreadingHTTPServer(socketserver.ThreadingMixIn, http.server.HTTPServer):
    daemon_threads = True
    allow_reuse_address = True


def start_local_range_proxy(fetcher: ResilientRangeFetcher) -> tuple[_ThreadingHTTPServer, str]:
    total_size = fetcher.get_total_size()

    class ProxyHandler(http.server.BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, format: str, *args) -> None:
            return  # Keep go-pmtiles progress bar clean

        def do_HEAD(self) -> None:
            self.send_response(200)
            self.send_header("Accept-Ranges", "bytes")
            self.send_header("Content-Length", str(total_size))
            self.send_header("Content-Type", "application/octet-stream")
            self.send_header("Connection", "close")
            self.end_headers()

        def do_GET(self) -> None:
            rng = self.headers.get("Range", "")
            start, end = 0, total_size - 1
            status = 200
            if rng.startswith("bytes="):
                status = 206
                spec = rng[6:].split(",")[0].strip()
                s_part, e_part = spec.split("-", 1)
                if s_part:
                    start = int(s_part)
                    end = int(e_part) if e_part else (total_size - 1)
                else:
                    suffix = int(e_part)
                    start = max(0, total_size - suffix)
                    end = total_size - 1
            end = min(end, total_size - 1)
            length = end - start + 1

            self.send_response(status)
            self.send_header("Accept-Ranges", "bytes")
            self.send_header("Content-Type", "application/octet-stream")
            self.send_header("Content-Length", str(length))
            if status == 206:
                self.send_header("Content-Range", f"bytes {start}-{end}/{total_size}")
            self.send_header("Connection", "close")
            self.end_headers()

            try:
                fetcher.stream_range(start, end, lambda chunk, _tot: self.wfile.write(chunk))
                self.wfile.flush()
            except (BrokenPipeError, ConnectionResetError):
                return

    srv = _ThreadingHTTPServer(("127.0.0.1", 0), ProxyHandler)
    port = srv.server_address[1]
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    return srv, f"http://127.0.0.1:{port}/source.pmtiles"


# ==============================================================================
# PMTiles v3 Binary Directory & Integrity Inspector
# ==============================================================================


def _read_varint(buf: bytes, pos: int) -> tuple[int, int]:
    res, shift = 0, 0
    while True:
        b = buf[pos]
        pos += 1
        res |= (b & 0x7F) << shift
        if not (b & 0x80):
            return res, pos
        shift += 7


def _parse_directory(raw_gz: bytes) -> list[tuple[int, int, int, int]]:
    buf = gzip.decompress(raw_gz)
    n, pos = _read_varint(buf, 0)
    tile_ids: list[int] = []
    run_lens: list[int] = []
    lengths: list[int] = []
    offsets: list[int] = []
    acc = 0
    for _ in range(n):
        d, pos = _read_varint(buf, pos)
        acc += d
        tile_ids.append(acc)
    for _ in range(n):
        r, pos = _read_varint(buf, pos)
        run_lens.append(r)
    for _ in range(n):
        ln, pos = _read_varint(buf, pos)
        lengths.append(ln)
    for i in range(n):
        o, pos = _read_varint(buf, pos)
        offsets.append((offsets[i - 1] + lengths[i - 1]) if (o == 0 and i > 0) else (o - 1))
    return list(zip(tile_ids, run_lens, offsets, lengths))


def _tile_id_to_zoom(tile_id: int) -> int:
    z = 0
    acc = 0
    while z <= 30:
        count = 1 << (2 * z)
        if tile_id < acc + count:
            return z
        acc += count
        z += 1
    return 0


def find_sparse_holes(fd: int, start_offset: int, file_size: int) -> list[tuple[int, int]]:
    """Use Linux SEEK_HOLE / SEEK_DATA to find unwritten sparse holes in microseconds."""
    if not hasattr(os, "SEEK_HOLE") or not hasattr(os, "SEEK_DATA"):
        return []
    holes: list[tuple[int, int]] = []
    pos = max(0, start_offset)
    while pos < file_size:
        try:
            h_start = os.lseek(fd, pos, os.SEEK_HOLE)
        except OSError:
            break
        if h_start >= file_size:
            break
        try:
            h_end = os.lseek(fd, h_start, os.SEEK_DATA)
        except OSError:
            h_end = file_size
        if h_end > h_start:
            holes.append((h_start, h_end))
        pos = h_end
    return holes


def _read_gzip_member_at(fd: int, offset: int, max_scan: int = 16 * 1024 * 1024) -> tuple[bytes, int]:
    """Decompress one gzip stream starting at offset and return (decompressed_bytes, compressed_length)."""
    decomp = zlib.decompressobj(31)
    out = bytearray()
    pos = offset
    consumed = 0
    while not decomp.eof:
        chunk = os.pread(fd, min(65536, max_scan - consumed), pos)
        if not chunk:
            raise EOFError(f"Incomplete gzip stream at offset {offset}")
        out.extend(decomp.decompress(chunk))
        used = len(chunk) - len(decomp.unused_data)
        consumed += used
        pos += used
        if consumed >= max_scan and not decomp.eof:
            raise ValueError(f"Gzip stream exceeded {max_scan} bytes at offset {offset}")
    return bytes(out), consumed


def verify_pmtiles_archive(filepath: str, pmtiles_bin: str = "", quiet: bool = False) -> tuple[bool, str]:
    """Deep integrity verification of a local PMTiles v3 archive."""
    if not os.path.isfile(filepath):
        return False, f"File does not exist: {filepath}"

    fd = os.open(filepath, os.O_RDONLY)
    try:
        st = os.fstat(fd)
        if st.st_size < HEADER_SIZE:
            return False, f"File is truncated ({st.st_size} bytes < 127-byte header)"

        hdr = os.pread(fd, HEADER_SIZE, 0)
        if hdr[:7] != b"PMTiles":
            holes = find_sparse_holes(fd, 127, st.st_size)
            missing = sum(b - a for a, b in holes)
            if hdr == b"\x00" * HEADER_SIZE and st.st_size > 1024:
                pct_done = 100.0 * (st.st_size - missing) / st.st_size
                return (
                    False,
                    f"INCOMPLETE / INTERRUPTED EXTRACT: 127-byte header is all zeros (go-pmtiles crashed before "
                    f"finishing); apparent size={fmt_bytes(st.st_size)}, written={pct_done:.1f}%, "
                    f"missing sparse holes={fmt_bytes(missing)}.",
                )
            return False, f"Invalid PMTiles magic bytes: {hdr[:7]!r}"

        vals = struct.unpack(HEADER_FMT, hdr)
        ver = vals[1]
        if ver != 3:
            return False, f"Unsupported PMTiles spec version: {ver}"

        root_off, root_len, meta_off, meta_len, leaf_off, leaf_len, tile_off, tile_len = vals[2:10]
        min_z, max_z = vals[17], vals[18]

        for sec_name, off, ln in (
            ("root_dir", root_off, root_len),
            ("json_metadata", meta_off, meta_len),
            ("leaf_dirs", leaf_off, leaf_len),
            ("tile_data", tile_off, tile_len),
        ):
            if off + ln > st.st_size:
                return False, f"Section '{sec_name}' ({off}+{ln}) exceeds file size ({st.st_size})"

        if tile_len == 0:
            return False, "Archive contains 0 bytes of tile_data"

        # 1. Check for unwritten filesystem sparse holes anywhere from tile_data / metadata / leaf_dirs
        scan_from = min(x for x in (meta_off, tile_off, leaf_off if leaf_len > 0 else st.st_size) if x > 0)
        holes = find_sparse_holes(fd, scan_from, st.st_size)
        if holes:
            missing = sum(b - a for a, b in holes)
            return (
                False,
                f"CORRUPT / SPARSE HOLES DETECTED: {len(holes)} unwritten hole(s) totaling "
                f"{fmt_bytes(missing)} inside archive body (first hole at offset {holes[0][0]}).",
            )

        # 2. Decompress root directory, metadata, and spot-check first + highest-offset physical tiles
        root_entries = _parse_directory(os.pread(fd, root_len, root_off))
        _ = json.loads(gzip.decompress(os.pread(fd, meta_len, meta_off)).decode("utf-8"))

        # Find first physical tile
        cur_entries = root_entries
        while cur_entries and cur_entries[0][1] == 0:
            cur_entries = _parse_directory(os.pread(fd, cur_entries[0][3], leaf_off + cur_entries[0][2]))
        if not cur_entries:
            return False, "Directory tree contains no tile entries"
        first_tile = cur_entries[0]

        # Find physical tile nearest the very end of tile_data
        tail_entries = root_entries
        while tail_entries and tail_entries[-1][1] == 0:
            tail_entries = _parse_directory(os.pread(fd, tail_entries[-1][3], leaf_off + tail_entries[-1][2]))
        phys_tiles = [e for e in tail_entries if e[1] > 0]
        last_phys = max(phys_tiles, key=lambda e: e[2] + e[3])

        for label, entry in (("first", first_tile), ("tail", last_phys)):
            _, _, t_off, t_len = entry
            raw_tile = os.pread(fd, t_len, tile_off + t_off)
            if len(raw_tile) != t_len:
                return False, f"Truncated {label} tile at {tile_off + t_off}"
            if vals[15] == 2:  # gzip tile compression
                decomp_tile = gzip.decompress(raw_tile)
                if len(decomp_tile) == 0 and t_len > 32:
                    return False, f"Empty decompressed {label} tile"
    except Exception as exc:
        return False, f"Verification exception: {exc}"
    finally:
        os.close(fd)

    # 3. Run go-pmtiles structural verifier if binary is available
    if pmtiles_bin and os.path.isfile(pmtiles_bin) and os.access(pmtiles_bin, os.X_OK):
        res = subprocess.run([pmtiles_bin, "verify", filepath], capture_output=True, text=True)
        if res.returncode != 0:
            err_msg = (res.stderr or res.stdout).strip()
            return False, f"`pmtiles verify` failed: {err_msg}"

    summary = (
        f"OK ({fmt_bytes(st.st_size)}, zooms {min_z}-{max_z}, "
        f"zero sparse holes, directories & tail tiles verified)"
    )
    if not quiet:
        print(f"==> Integrity check PASSED for {filepath}: {summary}")
    return True, summary


# ==============================================================================
# In-Place Resume & Header Reconstruction for Interrupted Full-Planet Extracts
# ==============================================================================


def _reconstruct_interrupted_planet_header(fd: int, file_size: int, remote_hdr: bytes) -> tuple[bytes, int, int]:
    """
    Inspect an interrupted `go-pmtiles extract` file (where [0..127] is still zeros,
    [127..tile_off] holds Root Dir + Metadata + Leaf Dirs, and [tile_off..EOF] holds tile_data)
    and reconstruct its exact 127-byte PMTiles v3 header.
    Returns (header_127_bytes, local_tile_off, local_tile_len).
    """
    r_vals = struct.unpack(HEADER_FMT, remote_hdr)

    # 1. Read gzip Root Directory starting at offset 127
    root_off = 127
    _, root_len = _read_gzip_member_at(fd, root_off)
    root_entries = _parse_directory(os.pread(fd, root_len, root_off))

    # 2. Read gzip JSON Metadata immediately following Root Directory
    meta_off = root_off + root_len
    _, meta_len = _read_gzip_member_at(fd, meta_off)

    # 3. Compute Leaf Directories span from leaf pointers in Root Directory
    leaf_off = meta_off + meta_len
    leaf_ptrs = [(off, ln) for _, rl, off, ln in root_entries if rl == 0]
    leaf_len = max((off + ln for off, ln in leaf_ptrs), default=0)

    tile_off = leaf_off + leaf_len
    if tile_off >= file_size:
        raise ValueError(f"Computed tile_off ({tile_off}) >= file_size ({file_size})")
    tile_len = file_size - tile_off

    # Ensure [127 .. tile_off] itself has zero sparse holes
    dir_holes = find_sparse_holes(fd, 127, tile_off)
    if dir_holes:
        raise ValueError("Directory section [127..tile_off] contains sparse holes; cannot resume in-place")

    # 4. Walk Root + Leaf directories to compute exact tile counts & max_zoom
    addressed_tiles = 0
    tile_entries = 0
    unique_offsets: set[int] = set()
    max_tile_id = 0

    for tid, rl, off, ln in root_entries:
        if rl > 0:
            addressed_tiles += rl
            tile_entries += 1
            unique_offsets.add(off)
            end_tid = tid + rl - 1
            if end_tid > max_tile_id:
                max_tile_id = end_tid

    for l_off, l_ln in leaf_ptrs:
        l_entries = _parse_directory(os.pread(fd, l_ln, leaf_off + l_off))
        for tid, rl, off, ln in l_entries:
            if rl > 0:
                addressed_tiles += rl
                tile_entries += 1
                unique_offsets.add(off)
                end_tid = tid + rl - 1
                if end_tid > max_tile_id:
                    max_tile_id = end_tid

    tile_contents = len(unique_offsets)
    max_z = _tile_id_to_zoom(max_tile_id) if tile_entries > 0 else 0

    hdr_bytes = struct.pack(
        HEADER_FMT,
        b"PMTiles",
        3,
        root_off,
        root_len,
        meta_off,
        meta_len,
        leaf_off,
        leaf_len,
        tile_off,
        tile_len,
        addressed_tiles,
        tile_entries,
        tile_contents,
        1,              # clustered = true
        r_vals[14],     # internal_compression (gzip = 2)
        r_vals[15],     # tile_compression (gzip = 2)
        r_vals[16],     # tile_type (mvt = 1)
        0,              # min_zoom = 0
        max_z,          # max_zoom
        r_vals[19],     # min_lon_e7
        r_vals[20],     # min_lat_e7
        r_vals[21],     # max_lon_e7
        r_vals[22],     # max_lat_e7
        r_vals[23],     # center_zoom
        r_vals[24],     # center_lon_e7
        r_vals[25],     # center_lat_e7
    )
    return hdr_bytes, tile_off, tile_len


def try_resume_planet_part(
    part_path: str,
    fetcher: ResilientRangeFetcher,
) -> bool:
    """
    Resume an interrupted full-planet `.part` archive in-place by filling only its
    missing sparse byte ranges from the remote PMTiles archive and writing the 127-byte header.
    Returns True if resume succeeded, or False if caller should fall back to a fresh extract.
    """
    if not os.path.isfile(part_path):
        return False

    remote_hdr, _ = fetcher.fetch_bytes(0, HEADER_SIZE - 1)
    if remote_hdr[:7] != b"PMTiles":
        return False
    r_vals = struct.unpack(HEADER_FMT, remote_hdr)
    remote_tile_off = r_vals[8]
    remote_tile_len = r_vals[9]

    fd = os.open(part_path, os.O_RDWR)
    try:
        st = os.fstat(fd)
        if st.st_size < 4096:
            return False

        cur_hdr = os.pread(fd, HEADER_SIZE, 0)
        if cur_hdr[:7] == b"PMTiles":
            l_vals = struct.unpack(HEADER_FMT, cur_hdr)
            reconstructed_hdr = cur_hdr
            tile_off, tile_len = l_vals[8], l_vals[9]
        elif cur_hdr == b"\x00" * HEADER_SIZE:
            reconstructed_hdr, tile_off, tile_len = _reconstruct_interrupted_planet_header(
                fd, st.st_size, remote_hdr
            )
        else:
            return False

        if tile_len > remote_tile_len:
            return False

        holes = find_sparse_holes(fd, tile_off, tile_off + tile_len)
        if not holes:
            # All tile bytes are already on disk; ensure the 127-byte header is written
            if cur_hdr[:7] != b"PMTiles":
                os.pwrite(fd, reconstructed_hdr, 0)
                os.fsync(fd)
            return True

        # Merge nearby holes and rewind each start by 1 MiB to cover any partial page at crash boundary
        merged: list[tuple[int, int]] = []
        for h_start, h_end in holes:
            s = max(tile_off, h_start - REWIND_SAFETY_BYTES)
            e = min(tile_off + tile_len, h_end)
            if merged and s <= merged[-1][1] + REWIND_SAFETY_BYTES:
                merged[-1] = (merged[-1][0], max(merged[-1][1], e))
            else:
                merged.append((s, e))

        # Verify 4 KiB seam immediately before first hole matches remote archive byte-for-byte
        first_s = merged[0][0]
        if first_s - tile_off >= 4096:
            seam_local = os.pread(fd, 4096, first_s - 4096)
            seam_remote_off = remote_tile_off + (first_s - tile_off) - 4096
            seam_remote, _ = fetcher.fetch_bytes(seam_remote_off, seam_remote_off + 4095)
            if seam_local != seam_remote:
                print(
                    "==> Partial file came from a different daily build than the target source URL; "
                    "starting a clean extraction instead."
                )
                return False

        bytes_needed = sum(e - s for s, e in merged)
        already_have = tile_len - bytes_needed
        print(
            f"==> Resuming existing partial archive at {100.0 * already_have / tile_len:.1f}% "
            f"({fmt_bytes(already_have)} already on disk, {fmt_bytes(bytes_needed)} remaining) ..."
        )

        t0 = time.monotonic()
        done_now = 0
        last_print = 0.0

        for s, e in merged:
            rem_start = remote_tile_off + (s - tile_off)
            rem_end = remote_tile_off + (e - tile_off) - 1
            write_pos = [s]

            def on_chunk(chunk: bytes, _tot: int | None) -> None:
                nonlocal done_now, last_print
                os.pwrite(fd, chunk, write_pos[0])
                write_pos[0] += len(chunk)
                done_now += len(chunk)
                now = time.monotonic()
                if now - last_print >= 0.5 or done_now == bytes_needed:
                    last_print = now
                    elapsed = max(0.001, now - t0)
                    rate = done_now / elapsed
                    total_done = already_have + done_now
                    pct = 100.0 * total_done / tile_len
                    eta = (bytes_needed - done_now) / rate if rate > 0 else 0.0
                    sys.stdout.write(
                        f"\r  Resuming: {pct:5.1f}% ({fmt_bytes(total_done)} / {fmt_bytes(tile_len)}) "
                        f"| {fmt_bytes(rate)}/s | ETA {fmt_duration(eta)}   "
                    )
                    sys.stdout.flush()

            fetcher.stream_range(rem_start, rem_end, on_chunk)

        sys.stdout.write("\n")
        sys.stdout.flush()

        # Write reconstructed 127-byte header and sync to disk
        os.pwrite(fd, reconstructed_hdr, 0)
        os.fsync(fd)
        return True
    except KeyboardInterrupt:
        os.fsync(fd)
        raise
    except Exception as exc:
        print(f"==> In-place resume could not be applied ({exc}); falling back to clean extraction.")
        return False
    finally:
        os.close(fd)


# ==============================================================================
# High-Level Orchestrator (`extract` & `verify` subcommands)
# ==============================================================================


def run_extract(args: argparse.Namespace) -> int:
    dest_path = os.path.abspath(args.output)
    part_path = dest_path + ".part"
    meta_path = dest_path + ".part.json"
    is_planet = not bool(args.bbox)

    os.makedirs(os.path.dirname(dest_path), exist_ok=True)

    # 1. Check if destination file already exists on disk
    if os.path.isfile(dest_path) and not args.force:
        ok, msg = verify_pmtiles_archive(dest_path, pmtiles_bin=args.pmtiles_bin, quiet=True)
        if ok:
            print(f"==> Destination archive already exists and passed verification: {dest_path}")
            print(f"    {msg}")
            print("    (Pass --force to re-download from scratch.)")
            return 0
        else:
            print(f"==> Detected incomplete/damaged existing archive at {dest_path}:")
            print(f"    {msg}")
            if is_planet and not os.path.exists(part_path):
                print(f"==> Moving {os.path.basename(dest_path)} -> {os.path.basename(part_path)} to attempt repair/resume...")
                os.replace(dest_path, part_path)

    # 2. If resuming an existing `.part` file, honor its pinned source_url unless user explicitly forced one
    source_url = args.source_url
    if os.path.isfile(part_path) and os.path.isfile(meta_path) and not args.pinned_source:
        try:
            with open(meta_path, "r") as mf:
                saved = json.load(mf)
            if (
                saved.get("maxzoom") == args.maxzoom
                and (saved.get("bbox") or "") == (args.bbox or "")
                and saved.get("source_url")
            ):
                source_url = saved["source_url"]
                print(f"==> Pinned resume source URL from previous session: {source_url}")
        except Exception:
            pass

    fetcher = ResilientRangeFetcher(source_url)

    # 3. Attempt zero-waste in-place resume if a full-planet `.part` file is present
    resumed = False
    if is_planet and os.path.isfile(part_path) and not args.force:
        resumed = try_resume_planet_part(part_path, fetcher)

    # 4. Otherwise run `go-pmtiles extract` through our local resilient HTTP/1.1 chunked range proxy
    if not resumed:
        if os.path.exists(part_path):
            os.remove(part_path)
        with open(meta_path, "w") as mf:
            json.dump(
                {
                    "source_url": source_url,
                    "maxzoom": args.maxzoom,
                    "bbox": args.bbox or "",
                    "started_at": int(time.time()),
                },
                mf,
                indent=2,
            )

        srv, local_url = start_local_range_proxy(fetcher)
        try:
            cmd = [
                args.pmtiles_bin,
                "extract",
                local_url,
                part_path,
                f"--maxzoom={args.maxzoom}",
            ]
            if args.bbox:
                cmd.append(f"--bbox={args.bbox}")

            env = os.environ.copy()
            env["GODEBUG"] = "http2client=0"
            print("==> Streaming via local resilient HTTP/1.1 range-slicing proxy (32 MiB chunks, auto-retry)...")
            rc = subprocess.call(cmd, env=env)
            if rc != 0:
                print(
                    f"\nError: Extraction interrupted (exit code {rc}). "
                    f"Partial state preserved at {part_path} — re-run the same command to resume!",
                    file=sys.stderr,
                )
                return rc
        except KeyboardInterrupt:
            print(
                f"\nInterrupted! Partial state preserved at {part_path} — "
                f"re-run the same command anytime to resume where it left off.",
                file=sys.stderr,
            )
            return 130
        finally:
            srv.shutdown()
            srv.server_close()

    # 5. Deep post-download integrity gate before atomic rename
    ok, msg = verify_pmtiles_archive(part_path, pmtiles_bin=args.pmtiles_bin, quiet=False)
    if not ok:
        print(f"Error: Post-download verification FAILED for {part_path}: {msg}", file=sys.stderr)
        return 1

    os.replace(part_path, dest_path)
    if os.path.exists(meta_path):
        os.remove(meta_path)
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="CVEMAP Resilient PMTiles Helper")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_ext = sub.add_parser("extract", help="Resilient & resumable PMTiles extraction")
    p_ext.add_argument("--source-url", required=True)
    p_ext.add_argument("--output", required=True)
    p_ext.add_argument("--maxzoom", type=int, required=True)
    p_ext.add_argument("--bbox", default="")
    p_ext.add_argument("--pmtiles-bin", required=True)
    p_ext.add_argument("--pinned-source", action="store_true")
    p_ext.add_argument("--force", action="store_true")

    p_ver = sub.add_parser("verify", help="Deep integrity check of a local PMTiles file")
    p_ver.add_argument("filepath")
    p_ver.add_argument("--pmtiles-bin", default="")

    args = parser.parse_args()
    if args.cmd == "verify":
        ok, msg = verify_pmtiles_archive(args.filepath, pmtiles_bin=args.pmtiles_bin, quiet=False)
        if not ok:
            print(f"==> Integrity check FAILED for {args.filepath}:\n    {msg}", file=sys.stderr)
            return 1
        return 0
    return run_extract(args)


if __name__ == "__main__":
    sys.exit(main())
