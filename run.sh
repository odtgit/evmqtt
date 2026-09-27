#!/usr/bin/env sh
set -e

CONFIG_FILE="/data/options.json"

if [ -f "$CONFIG_FILE" ]; then
    LOG_LEVEL=$(python3 -c "import json; print(json.load(open('$CONFIG_FILE')).get('log_level', 'info'))")
else
    LOG_LEVEL="info"
fi

echo "[INFO] Starting evmqtt (log level: ${LOG_LEVEL})"

case "$LOG_LEVEL" in
    debug)
        ARGS="--debug"
        ;;
    info)
        ARGS="--verbose"
        ;;
    *)
        ARGS=""
        ;;
esac

echo "[INFO] Available input devices:"
ls -la /dev/input/ 2>/dev/null || echo "[WARNING] Cannot list /dev/input/"

exec evmqtt ${ARGS}
