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
RUN pip install --no-cache-dir -r requirements.txt
COPY vendor/codebuddy2api/core ./core
COPY vendor/codebuddy2api/admin ./admin
COPY unified ./unified
COPY --from=native /libtrae.so ./libtrae.so
ENV PYTHONUNBUFFERED=1
CMD ["python", "-m", "unified.server"]
