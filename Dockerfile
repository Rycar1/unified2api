FROM python:3.12-slim-bookworm AS qoder-umid
WORKDIR /build
COPY vendor/qoder2api-hub/_install_umid.py ./
RUN python _install_umid.py --platform linux --dest /build/umid

FROM golang:1.23-bookworm AS native
WORKDIR /src
COPY vendor/trae2api ./vendor/trae2api
COPY vendor/monkeycode2api ./vendor/monkeycode2api
COPY native ./native
WORKDIR /src/native
RUN go mod tidy && CGO_ENABLED=1 go build -buildmode=c-shared -trimpath -o /libtrae.so .

FROM node:22-bookworm-slim AS opencode-runtime
WORKDIR /runtime
COPY vendor/opencode2api/package*.json ./
RUN npm ci --omit=dev --ignore-scripts && npm install --prefix /opt/opencode -g opencode-ai@1.19.0
COPY vendor/opencode2api ./

FROM python:3.12-slim-bookworm
WORKDIR /app
RUN apt-get update && apt-get install -y --no-install-recommends libstdc++6 ca-certificates \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd --gid 10001 opencode-runtime \
    && useradd --uid 10001 --gid 10001 --no-create-home --shell /usr/sbin/nologin opencode-runtime
COPY --from=opencode-runtime /usr/local/bin/node /usr/local/bin/node
COPY --from=opencode-runtime /opt/opencode /opt/opencode
COPY --from=opencode-runtime /runtime ./vendor/opencode2api
ENV PATH="/opt/opencode/bin:${PATH}"
COPY vendor/codebuddy2api/requirements.txt ./requirements.txt
RUN PIP_CONFIG_FILE=/dev/null PIP_INDEX_URL=https://pypi.org/simple pip install --no-cache-dir -r requirements.txt
COPY vendor/codebuddy2api/core ./core
COPY vendor/codebuddy2api/admin ./admin
COPY vendor/workbuddy2api-hub ./vendor/workbuddy2api-hub
COPY vendor/qoder2api-hub ./vendor/qoder2api-hub
COPY --from=qoder-umid /build/umid ./vendor/qoder2api-hub/umid
COPY unified ./unified
COPY --from=native /libtrae.so ./libtrae.so
RUN ln -s /opt/opencode/bin/opencode /usr/local/bin/opencode
ARG UNIFIED_RELEASE_REVISION=dev
LABEL io.unified2api.role="gateway" org.opencontainers.image.source="https://github.com/Rycar1/unified2api" org.opencontainers.image.revision="${UNIFIED_RELEASE_REVISION}"
ENV PYTHONUNBUFFERED=1 WORKBUDDY_EMBEDDED=true WORKBUDDY_HUB_URL=http://127.0.0.1:8788 QD_UMID_DIR=/app/vendor/qoder2api-hub/umid UNIFIED_RELEASE_REVISION=${UNIFIED_RELEASE_REVISION}
CMD ["python", "-m", "unified.server"]
