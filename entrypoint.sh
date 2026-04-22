#!/bin/sh
# If running as root: fix /data ownership then drop to appuser via gosu.
# If already running as a non-root user (e.g. user: "1000:1000" in compose): just exec directly.
if [ "$(id -u)" = "0" ]; then
    chown -R appuser:appuser /data 2>/dev/null || true
    exec gosu appuser "$@"
fi
exec "$@"
