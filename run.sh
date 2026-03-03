#!/usr/bin/env bash
# Launch StreamsClient.
# Usage:
#   ./run.sh                  # normal start (restores session or shows picker)
#   ./run.sh -e               # start empty (no streams)
#   ./run.sh -p playlist.m3u  # load a specific playlist
set -euo pipefail
cd "$(dirname "$0")"

if [ -d .venv ]; then
    source .venv/bin/activate
fi

exec python src/streams_client.py "$@"
