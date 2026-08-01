#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")"

if [ ! -d ".venv" ]; then
    python3 -m venv .venv
fi

./.venv/bin/pip install --quiet --upgrade pip
./.venv/bin/pip install --quiet -r requirements.txt

# Secrets (DISCORD_BOT_TOKEN, optionally DISCORD_GUILD_ID) are never committed.
# .env lives only on the host, out of band, in this persistent working directory.
if [ -f ".env" ]; then
    set -a
    source .env
    set +a
fi

./.venv/bin/python -m app.migrate

exec ./.venv/bin/python -m app
