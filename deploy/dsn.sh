#!/bin/sh
# Print the DATABASE_URL from .env in the form psql and pg_dump accept.
#
# Exists because every runbook step needs the DSN, and a shell variable set in
# one command does not survive into the next when each is run in its own shell
# (as Claude Code's `!` prefix does). Use it inline:
#
#   psql "$(deploy/dsn.sh)" -c 'SELECT count(*) FROM statistics;'
#   pg_dump --no-owner --no-acl "$(deploy/dsn.sh)" > backup.sql
#
# SQLAlchemy's postgresql+asyncpg:// prefix is a driver selector that libpq
# does not understand, so it is stripped.
set -eu

cd "$(dirname "$0")/.."

if [ ! -r .env ]; then
    echo "deploy/dsn.sh: cannot read .env in $(pwd)" >&2
    exit 1
fi

dsn=$(grep -E '^DATABASE_URL=' .env \
    | sed -E 's/^DATABASE_URL="?//; s/"?$//; s#^postgresql\+asyncpg#postgresql#')

if [ -z "$dsn" ]; then
    echo "deploy/dsn.sh: DATABASE_URL is not set in .env" >&2
    exit 1
fi

printf '%s\n' "$dsn"
