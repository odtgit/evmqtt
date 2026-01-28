#!/bin/bash
# ==============================================================================
# evmqtt Home Assistant Add-on
# Starts the evmqtt service with auto-discovery support
# ==============================================================================

set -e

CONFIG_FILE="/data/options.json"

echo "[INFO] Starting evmqtt Home Assistant Add-on v1.1.0..."

# Read configuration from options.json
if [ -f "$CONFIG_FILE" ]; then
    LOG_LEVEL=$(python3 -c "import json; print(json.load(open('$CONFIG_FILE')).get('log_level', 'info'))")
    AUTO_DISCOVER=$(python3 -c "import json; print(json.load(open('$CONFIG_FILE')).get('auto_discover', True))")
    echo "[INFO] Log level: ${LOG_LEVEL}"
    echo "[INFO] Auto-discover: ${AUTO_DISCOVER}"
else
    LOG_LEVEL="info"
    AUTO_DISCOVER="True"
    echo "[WARNING] No options.json found, using defaults"
fi

# List available input devices for debugging
echo "[INFO] Available input devices:"
ls -la /dev/input/ 2>/dev/null || echo "[WARNING] Cannot list /dev/input/"

# Also list devices via evmqtt for more details
echo "[INFO] Detected input devices (via evmqtt):"
evmqtt --list-devices 2>/dev/null || echo "[WARNING] Could not list devices via evmqtt"

# Build command arguments
ARGS=""

# Map log level to evmqtt arguments
case "${LOG_LEVEL}" in
    debug)
        ARGS="--debug"
        ;;
    info)
        ARGS="--verbose"
        ;;
    *)
        # warning/error - no extra verbosity
        ;;
esac

echo "[INFO] Starting evmqtt with args: ${ARGS:-'(none)'}"
exec evmqtt ${ARGS}
