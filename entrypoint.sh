#!/bin/sh
# Fix /data ownership at runtime — the host volume mount replaces the image
# layer, so the chown in the Dockerfile doesn't survive. This runs as root,
# fixes permissions, then drops to appuser via gosu.
chown -R appuser:appuser /data
exec gosu appuser "$@"
