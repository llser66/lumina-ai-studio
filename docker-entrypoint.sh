#!/bin/sh
set -eu

mkdir -p "$DATA_DIR"
chown lumina:lumina "$DATA_DIR"
exec gosu lumina "$@"
