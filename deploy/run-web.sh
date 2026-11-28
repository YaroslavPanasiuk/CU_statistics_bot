#!/usr/bin/env sh
# Build and (re)start the configuration web UI.
#
# It is deliberately not published on a host port: Caddy reaches it over a
# shared Docker network, so the only way in is through the reverse proxy with
# TLS. The Telegram login widget refuses to work over plain HTTP anyway.
#
#   cd ~/Projects/CU_statistics_bot && deploy/run-web.sh
#
# The container joins two networks, which is the whole reason this script
# exists rather than a single docker run:
#
#   custat_net       to reach Postgres, which listens only on 127.0.0.1 of the
#                    host and so is unreachable from inside a container
#   caddy's network  so the reverse proxy can resolve it by name
set -eu

cd "$(dirname "$0")/.."

NAME=custat_config
DB_CONTAINER=Postgrees-custat
DB_NETWORK=custat_net

# The bot's own username, for the login widget's data-telegram-login. Prefer
# BOT_USERNAME in .env; this default is here so the script works without
# editing a root-owned file.
DEFAULT_BOT_USERNAME=CU_statistics_bot

if [ ! -f .env ]; then
    echo "error: .env is missing" >&2
    exit 1
fi

# The bot connects to Postgres on 127.0.0.1:5433. Inside a container that is
# the container's own loopback, so the host and port are rewritten to the
# database container's name on the shared network. Everything else in .env --
# the token, the admin ids, the credentials -- is passed through untouched.
DB_URL=$(grep -E '^DATABASE_URL=' .env | cut -d= -f2- | tr -d '"' \
    | sed -E "s#@[^/]+/#@${DB_CONTAINER}:5432/#")
if [ -z "$DB_URL" ]; then
    echo "error: DATABASE_URL is not set in .env" >&2
    exit 1
fi

CADDY_NETWORK="${CADDY_NETWORK:-$(docker inspect caddy \
    --format '{{range $k, $v := .NetworkSettings.Networks}}{{$k}}{{"\n"}}{{end}}' \
    2>/dev/null | head -1)}"
if [ -z "$CADDY_NETWORK" ]; then
    echo "error: cannot find the caddy container's network." >&2
    echo "       set CADDY_NETWORK=<network> and run again." >&2
    exit 1
fi

docker network inspect "$DB_NETWORK" > /dev/null 2>&1 \
    || docker network create "$DB_NETWORK"

docker build -f Dockerfile.web -t "$NAME" .

if [ -n "$(docker ps -aq --filter "name=^${NAME}\$")" ]; then
    echo "removing the previous container"
    docker rm -f "$NAME" > /dev/null
fi

docker run \
    --detach \
    --name "$NAME" \
    --env-file .env \
    --env "DATABASE_URL=$DB_URL" \
    --env "BOT_USERNAME=${BOT_USERNAME:-$DEFAULT_BOT_USERNAME}" \
    --restart unless-stopped \
    --network "$DB_NETWORK" \
    "$NAME" > /dev/null

# Caddy resolves containers by name, which only works on a network it is also
# on. Attached second so the primary network stays the database one.
docker network connect "$CADDY_NETWORK" "$NAME"

echo "started as '$NAME:8000' on '$DB_NETWORK' and '$CADDY_NETWORK'."
echo
echo "If this is the first run, add to ~/forgejo/Caddyfile:"
echo
echo "  custat.yarosiuk.duckdns.org {"
echo "      encode zstd gzip"
echo "      reverse_proxy $NAME:8000"
echo "  }"
echo
echo "then: docker exec caddy caddy reload --config /etc/caddy/Caddyfile"
