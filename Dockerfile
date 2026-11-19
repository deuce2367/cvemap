# syntax=docker/dockerfile:1
# ==============================================================================
# CVEMAP Unified Multi-Target Dockerfile
#   --target lite : Lightweight runtime image (mount /app/data at runtime)
#   --target full : Self-contained offline image (bundles map data into /app/data)
# ==============================================================================

# ==============================================================================
# Stage 1: Isolated Python dependency builder (keeps pip/venv wheels out of runtime)
# ==============================================================================
FROM maptiler/tileserver-gl:latest AS venv-builder

USER root
ENV DEBIAN_FRONTEND=noninteractive

COPY app/requirements.txt /tmp/requirements.txt
RUN apt-get update && \
    apt-get install -y --no-install-recommends python3 python3-venv && \
    python3 -m venv /opt/venv && \
    /opt/venv/bin/pip install --no-cache-dir -r /tmp/requirements.txt && \
    /opt/venv/bin/pip uninstall -y pip setuptools wheel || true && \
    find /opt/venv -type d -name "__pycache__" -exec rm -rf {} +

# ==============================================================================
# Stage 2: Hardened lightweight runtime image
# ==============================================================================
FROM maptiler/tileserver-gl:latest AS lite

ARG CVEMAP_UID=4038
ARG CVEMAP_GID=1000

USER root

ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    HOME="/home/cvemap" \
    PATH="/opt/venv/bin:${PATH}"

# Apply Ubuntu security updates, install Python runtime & essential ops utilities,
# and purge unused C -dev headers.
RUN apt-get update && \
    apt-get dist-upgrade -y --no-install-recommends && \
    apt-get install -y --no-install-recommends \
      bash-completion \
      ca-certificates \
      curl \
      htop \
      iproute2 \
      iputils-ping \
      jq \
      less \
      openssh-client \
      procps \
      python3 \
      rsync \
      sysstat \
      vim && \
    dpkg-query -W -f='${Package}\n' | \
      grep -Ev -- '(-dev$|^libc-dev-bin$|^linux-libc-dev$|^libglib2\.0-dev-bin$|^python3-pip-whl$|^python3-venv$)' | \
      xargs -r apt-mark manual >/dev/null && \
    dpkg-query -W -f='${Package}\n' | \
      grep -E -- '(-dev$|^libc-dev-bin$|^linux-libc-dev$|^libglib2\.0-dev-bin$|^python3-pip-whl$|^python3-venv$)' | \
      xargs -r apt-get purge -y --auto-remove && \
    mkdir -p -m 1777 /tmp/.X11-unix && \
    rm -rf /var/lib/apt/lists/* /var/cache/apt/archives/*

# Create configurable cvemap group and user (defaults: UID 4038, GID 1000)
RUN EXISTING_GRP="$(getent group "${CVEMAP_GID}" | cut -d: -f1 || true)" && \
    if [ -n "${EXISTING_GRP}" ]; then \
      if [ "${EXISTING_GRP}" != "cvemap" ]; then \
        groupmod -n cvemap "${EXISTING_GRP}"; \
      fi; \
    else \
      groupadd -g "${CVEMAP_GID}" cvemap; \
    fi && \
    EXISTING_USR="$(getent passwd "${CVEMAP_UID}" | cut -d: -f1 || true)" && \
    if [ -n "${EXISTING_USR}" ]; then \
      if [ "${EXISTING_USR}" != "cvemap" ]; then \
        usermod -l cvemap -d /home/cvemap -m -g cvemap -s /bin/bash "${EXISTING_USR}"; \
      fi; \
    else \
      useradd -m -d /home/cvemap -s /bin/bash -u "${CVEMAP_UID}" -g cvemap cvemap; \
    fi

# Patch TileServer-GL transitive Node deps, prune build-only node-gyp/tar, and strip npm CLI from runtime
RUN cd /usr/src/app && \
    npm install --no-save --no-audit --no-fund --ignore-scripts \
      "body-parser@^2.4.0" \
      "qs@^6.16.1" \
      "minimatch@^11.0.1" && \
    rm -rf \
      /usr/src/app/node_modules/@maplibre/maplibre-gl-native/node_modules/minimatch \
      /usr/src/app/node_modules/node-gyp \
      /usr/src/app/node_modules/tar \
      /usr/src/app/node_modules/@tootallnate/once \
      /usr/src/app/node_modules/cacache \
      /usr/src/app/node_modules/make-fetch-happen \
      /usr/src/app/node_modules/http-proxy-agent \
      /usr/lib/node_modules/npm \
      /usr/lib/node_modules/corepack \
      /usr/local/lib/node_modules/npm \
      /usr/local/lib/node_modules/corepack \
      /usr/bin/npm \
      /usr/bin/npx \
      /usr/bin/corepack \
      /usr/local/bin/npm \
      /usr/local/bin/npx \
      /usr/local/bin/corepack \
      /root/.npm

# Copy hardened Python virtual environment (no pip/wheel attack surface)
COPY --from=venv-builder /opt/venv /opt/venv

# Configure customized shell environment for cvemap user
COPY --chown=${CVEMAP_UID}:${CVEMAP_GID} app/shell/.bash_prompt /home/cvemap/.bash_prompt
COPY --chown=${CVEMAP_UID}:${CVEMAP_GID} app/shell/.bashrc /home/cvemap/.bashrc
COPY --chown=${CVEMAP_UID}:${CVEMAP_GID} app/shell/.vimrc /home/cvemap/.vimrc
RUN printf '[ -f ~/.bashrc ] && . ~/.bashrc\n' > /home/cvemap/.bash_profile && \
    chown -R "${CVEMAP_UID}:${CVEMAP_GID}" /home/cvemap

# Set up application & TileServer template assets under /app
WORKDIR /app
RUN mkdir -p /app/data /app/tileserver && \
    chown -R "${CVEMAP_UID}:${CVEMAP_GID}" /app

COPY --chown=${CVEMAP_UID}:${CVEMAP_GID} app/server /app/server
COPY --chown=${CVEMAP_UID}:${CVEMAP_GID} app/static /app/static
COPY --chown=${CVEMAP_UID}:${CVEMAP_GID} app/entrypoint.sh /app/entrypoint.sh
COPY --chown=${CVEMAP_UID}:${CVEMAP_GID} tileserver/config.json /app/tileserver/config.json
COPY --chown=${CVEMAP_UID}:${CVEMAP_GID} tileserver/fonts /app/tileserver/fonts
COPY --chown=${CVEMAP_UID}:${CVEMAP_GID} tileserver/sprites /app/tileserver/sprites
COPY --chown=${CVEMAP_UID}:${CVEMAP_GID} tileserver/styles /app/tileserver/styles

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

USER ${CVEMAP_UID}:${CVEMAP_GID}
EXPOSE 8000 8080

HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
  CMD curl -fsS http://127.0.0.1:8000/api/maps >/dev/null && curl -fsS http://127.0.0.1:8080/health >/dev/null || exit 1

ENTRYPOINT ["/app/entrypoint.sh"]

# ==============================================================================
# Full Offline Target: Bakes map data directly into /app/data
# ==============================================================================
FROM lite AS full

ARG CVEMAP_UID=4038
ARG CVEMAP_GID=1000
ARG DATA_SRC=app/data
COPY --chown=${CVEMAP_UID}:${CVEMAP_GID} ${DATA_SRC} /app/data
