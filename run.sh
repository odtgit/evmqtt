#!/bin/bash
# ==============================================================================
# evmqtt Home Assistant Add-on
# Starts the evmqtt service
# ==============================================================================

set -e

CONFIG_FILE="/data/options.json"

echo "[INFO] Starting evmqtt Home Assistant Add-on..."

# Read log level from options.json
if [ -f "$CONFIG_FILE" ]; then
    LOG_LEVEL=$(python3 -c "import json; print(json.load(open('$CONFIG_FILE')).get('log_level', 'info'))")
    echo "[INFO] Log level: ${LOG_LEVEL}"
else
    LOG_LEVEL="info"
    echo "[WARNING] No options.json found, using default log level: ${LOG_LEVEL}"
fi

# List available input devices for debugging
echo "[INFO] Available input devices:"
ls -la /dev/input/ 2>/dev/null || echo "[WARNING] Cannot list /dev/input/"

# Map log level to evmqtt arguments
case "${LOG_LEVEL}" in
    debug)
        exec evmqtt --debug
        ;;
    info)
        exec evmqtt --verbose
        ;;
    *)
        exec evmqtt
        ;;
esac
