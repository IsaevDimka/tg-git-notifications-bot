#!/bin/sh
# Volumes on Fly/Railway/Render are mounted root-owned. Start as root, hand DATA_DIR to the
# unprivileged "bot" user (uid 10001), then drop privileges for the actual process.
set -e
data="${DATA_DIR:-/data}"
if [ "$(id -u)" = "0" ]; then
  mkdir -p "$data"
  chown -R 10001:10001 "$data"
  exec setpriv --reuid=10001 --regid=10001 --clear-groups -- "$@"
fi
exec "$@"
