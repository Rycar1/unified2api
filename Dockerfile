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
FROM python:3.12-slim-bookworm
WORKDIR /app
COPY vendor/codebuddy2api/requirements.txt ./requirements.txt
RUN PIP_CONFIG_FILE=/dev/null PIP_INDEX_URL=https://pypi.org/simple pip install --no-cache-dir -r requirements.txt
COPY vendor/codebuddy2api/core ./core
COPY vendor/codebuddy2api/admin ./admin
COPY vendor/workbuddy2api-hub ./vendor/workbuddy2api-hub
COPY vendor/qoder2api-hub ./vendor/qoder2api-hub
COPY --from=qoder-umid /build/umid ./vendor/qoder2api-hub/umid
COPY unified ./unified
COPY --from=native /libtrae.so ./libtrae.so
ARG UNIFIED_RELEASE_REVISION=dev
LABEL io.unified2api.role="gateway" org.opencontainers.image.source="https://github.com/Rycar1/unified2api" org.opencontainers.image.revision="${UNIFIED_RELEASE_REVISION}"
ENV PYTHONUNBUFFERED=1 WORKBUDDY_EMBEDDED=true WORKBUDDY_HUB_URL=http://127.0.0.1:8788 QD_UMID_DIR=/app/vendor/qoder2api-hub/umid UNIFIED_RELEASE_REVISION=${UNIFIED_RELEASE_REVISION}
CMD ["python", "-m", "unified.server"]
