# syntax=docker/dockerfile:1
# ==============================================================================
# CVEMAP Unified Multi-Target Dockerfile
#   --target lite : Lightweight runtime image (mount /app/data at runtime)
#   --target full : Self-contained offline image (bundles map data into /app/data)
# ==============================================================================

FROM maptiler/tileserver-gl:latest AS lite

USER root

ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH="/opt/venv/bin:${PATH}"

# Install minimal Python runtime & process utilities (TileServer-GL + Xvfb are in base image)
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
      python3 \
      python3-venv \
      curl \
      procps && \
    mkdir -p -m 1777 /tmp/.X11-unix && \
    rm -rf /var/lib/apt/lists/*

# Set up isolated Python virtual environment for FastAPI + Pillow OGC WMS gateway
COPY app/requirements.txt /tmp/requirements.txt
RUN python3 -m venv /opt/venv && \
    /opt/venv/bin/pip install --no-cache-dir -r /tmp/requirements.txt && \
    rm -f /tmp/requirements.txt

# Set up application & TileServer template assets under /app
WORKDIR /app
RUN mkdir -p /app/data /app/tileserver && \
    chown -R 1000:1000 /app

COPY --chown=1000:1000 app/server /app/server
COPY --chown=1000:1000 app/static /app/static
COPY --chown=1000:1000 app/entrypoint.sh /app/entrypoint.sh
COPY --chown=1000:1000 tileserver/config.json /app/tileserver/config.json
COPY --chown=1000:1000 tileserver/fonts /app/tileserver/fonts
COPY --chown=1000:1000 tileserver/sprites /app/tileserver/sprites
COPY --chown=1000:1000 tileserver/styles /app/tileserver/styles

RUN chmod +x /app/entrypoint.sh && \
    ln -sfn "Noto Sans Regular" "/app/tileserver/fonts/Open Sans Regular" && \
    ln -sfn "Noto Sans Regular" "/app/tileserver/fonts/Arial Unicode MS Regular" && \
    ln -sfn "Noto Sans Italic" "/app/tileserver/fonts/Open Sans Italic" && \
    ln -sfn "Noto Sans Medium" "/app/tileserver/fonts/Open Sans Bold" && \
    for d in /app/static/vendor/font-glyphs/glyphs/*; do \
      [ -d "$d" ] || continue; \
      bn="$(basename "$d")"; \
      [ -e "/app/tileserver/fonts/$bn" ] || ln -s "$d" "/app/tileserver/fonts/$bn"; \
    done && \
    for sp_dir in /app/static/vendor/*/sprites; do \
      [ -d "$sp_dir" ] || continue; \
      vname="$(basename "$(dirname "$sp_dir")")"; \
      [ "$vname" = "protomaps-assets" ] && continue; \
      ln -sfn "$sp_dir" "/app/tileserver/sprites/$vname"; \
    done

USER 1000:1000
EXPOSE 8000 8080

HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
  CMD curl -fsS http://127.0.0.1:8000/api/maps >/dev/null && curl -fsS http://127.0.0.1:8080/health >/dev/null || exit 1

ENTRYPOINT ["/app/entrypoint.sh"]

# ==============================================================================
# Full Offline Target: Bakes map data directly into /app/data
# ==============================================================================
FROM lite AS full

ARG DATA_SRC=app/data
COPY --chown=1000:1000 ${DATA_SRC} /app/data
