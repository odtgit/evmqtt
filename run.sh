#!/usr/bin/env sh
set -e

echo "[INFO] Starting evmqtt"
echo "[INFO] Available input devices:"
ls -la /dev/input/ 2>/dev/null || echo "[WARNING] Cannot list /dev/input/"

exec evmqtt "$@"
