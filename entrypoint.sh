#!/bin/bash
# pocketfox entrypoint — starts supercronic (scheduled tasks) + pocketfox gateway

set -e

# Start supercronic in the background with the agent's crontab
supercronic "${HOME}/.config/pocketfox/crontab" &

# Start pocketfox gateway in the foreground
exec pocketfox gateway
