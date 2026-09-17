FROM golang:1.23-alpine AS build
WORKDIR /src
COPY . .
RUN CGO_ENABLED=0 go build -trimpath -o /gateway .
FROM alpine:3.20
RUN apk add --no-cache ca-certificates && adduser -D app
USER app
COPY --from=build /gateway /gateway
ENTRYPOINT ["/gateway"]
