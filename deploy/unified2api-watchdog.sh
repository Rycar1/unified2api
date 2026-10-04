#!/bin/sh
set -eu

PROJECT_DIR=/opt/unified2api-20260928-workbuddy
COMPOSE_FILE="$PROJECT_DIR/compose.yaml"
CONTAINER=unified2api-app-1

exec 9>/run/unified2api-watchdog.lock
flock -n 9 || exit 0

state=$(docker inspect --format '{{.State.Status}} {{if .State.Health}}{{.State.Health.Status}}{{end}}' "$CONTAINER" 2>/dev/null || true)

case "$state" in
    'running healthy'|'running starting'|'running ')
        exit 0
        ;;
    'running unhealthy')
        echo "$CONTAINER is unhealthy; recreating gateway"
        ;;
    *)
        echo "$CONTAINER is unavailable ($state); recreating gateway"
        ;;
esac

timeout 90 docker compose -f "$COMPOSE_FILE" --project-directory "$PROJECT_DIR" up -d --no-deps --force-recreate app
