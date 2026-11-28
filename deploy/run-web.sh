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

# Read .env through the shell, not with --env-file: docker passes the file
# verbatim, so ADMIN_IDS="1,2" arrives with the quotation marks still attached
# and the config refuses to start on a non-numeric id. Sourcing strips them the
# same way systemd and python-dotenv do for the bot.
set -a
. ./.env
set +a

# The bot connects to Postgres on 127.0.0.1:5433. Inside a container that is
# the container's own loopback, so the host and port are rewritten to the
# database container's name on the shared network.
DB_URL=$(printf '%s' "${DATABASE_URL:-}" | sed -E "s#@[^/]+/#@${DB_CONTAINER}:5432/#")
if [ -z "$DB_URL" ]; then
    echo "error: DATABASE_URL is not set in .env" >&2
    exit 1
fi
if [ -z "${BOT_TOKEN:-}" ]; then
    echo "error: BOT_TOKEN is not set in .env; logins cannot be verified" >&2
    exit 1
fi
if [ -z "${ADMIN_IDS:-}" ]; then
    echo "error: ADMIN_IDS is not set in .env; nobody could sign in" >&2
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

# An explicit list rather than the whole file. The web UI never reads or
# writes a spreadsheet, so it has no business holding GOOGLE_CREDS_JSON -- the
# service-account key that can edit the roster and every volunteer's answers.
docker run \
    --detach \
    --name "$NAME" \
    --env "DATABASE_URL=$DB_URL" \
    --env "BOT_TOKEN=$BOT_TOKEN" \
    --env "ADMIN_IDS=$ADMIN_IDS" \
    --env "BOT_USERNAME=${BOT_USERNAME:-$DEFAULT_BOT_USERNAME}" \
    --env "SESSION_SECRET=${SESSION_SECRET:-}" \
    --env "TZ=${TZ:-Europe/Kyiv}" \
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
