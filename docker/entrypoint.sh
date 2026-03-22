#!/bin/bash
set -e

# Entrypoint script for Sonarr/Radarr Import Monitor

shutdown_handler() {
    echo "Shutting down Import Monitor..."
    if [ ! -z "$PYTHON_PID" ]; then
        kill -TERM "$PYTHON_PID" 2>/dev/null || true
        wait "$PYTHON_PID" 2>/dev/null || true
    fi
    exit 0
}

trap shutdown_handler SIGTERM SIGINT

APP_TYPE="${APP_TYPE:-sonarr}"

# Validate required environment variables based on app type
if [ "$APP_TYPE" = "radarr" ]; then
    if [ -z "$RADARR_URL" ]; then
        echo "ERROR: RADARR_URL is required for Radarr mode"
        exit 1
    fi
    if [ -z "$RADARR_API_KEY" ]; then
        echo "ERROR: RADARR_API_KEY is required for Radarr mode"
        exit 1
    fi
    if ! echo "$RADARR_API_KEY" | grep -qE '^[a-fA-F0-9]{32}$'; then
        echo "WARNING: RADARR_API_KEY doesn't match typical API key format (32 hex chars)"
    fi
    echo "Starting Radarr Import Monitor | URL: $RADARR_URL"
else
    if [ -z "$SONARR_URL" ]; then
        echo "ERROR: SONARR_URL is required"
        exit 1
    fi
    if [ -z "$SONARR_API_KEY" ]; then
        echo "ERROR: SONARR_API_KEY is required"
        exit 1
    fi
    if ! echo "$SONARR_API_KEY" | grep -qE '^[a-fA-F0-9]{32}$'; then
        echo "WARNING: SONARR_API_KEY doesn't match typical API key format (32 hex chars)"
    fi
    echo "Starting Sonarr Import Monitor | URL: $SONARR_URL"
fi

cd /app

python main.py "$@" &
PYTHON_PID=$!

wait "$PYTHON_PID"
