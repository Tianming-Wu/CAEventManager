#!/bin/sh
# Foreground runner for the lottery service.
# Use ./service.sh start to run it detached.
#
# flock --no-fork guarantees exactly one instance and keeps the pid stable,
# which is what service.sh relies on.
set -eu
cd "$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)"
umask 077
mkdir -p data
# host/port come from config.json (LOTTERY_HOST / LOTTERY_PORT still win).
exec flock --nonblock --no-fork ./data/service.lock \
    python3 -u app.py >>data/service.log 2>&1
