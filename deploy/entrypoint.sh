#!/bin/sh
# The volume is mounted at /data when the machine starts; the image's links point into it.
set -e
mkdir -p /data/library /data/battles /data/vgc
exec "$@"
