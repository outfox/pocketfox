#!/bin/bash
# pocketfox entrypoint — starts supercronic (scheduled tasks) + pocketfox gateway

set -e

CRONTAB_PATH="${HOME}/.config/pocketfox/crontab"

# Pre-flight: ensure crontab exists and is readable
if [ ! -r "${CRONTAB_PATH}" ]; then
    echo "ERROR: crontab not found or not readable: ${CRONTAB_PATH}" >&2
    exit 1
fi

# Start supercronic in the background, capture PID for signal forwarding
supercronic "${CRONTAB_PATH}" &
SUPERCRONIC_PID=$!

# Forward SIGTERM/SIGINT to supercronic and wait for it to exit
_term() {
    kill -TERM "$SUPERCRONIC_PID" 2>/dev/null
    wait "$SUPERCRONIC_PID"
}
trap _term TERM INT

# Run qmd update (fail the container if this errors out)
qmd update

# Start pocketfox gateway in the foreground
exec pocketfox gateway
