#!/bin/bash
set -e

# Entrypoint script for Sonarr Import Monitor Docker container

# Function to handle shutdown signals gracefully
shutdown_handler() {
    echo "Received shutdown signal, stopping Sonarr Import Monitor..."
    # Send SIGTERM to the Python process
    if [ ! -z "$PYTHON_PID" ]; then
        kill -TERM "$PYTHON_PID" 2>/dev/null || true
        wait "$PYTHON_PID" 2>/dev/null || true
    fi
    echo "Shutdown complete"
    exit 0
}

# Set up signal handlers
trap shutdown_handler SIGTERM SIGINT

# Validate required environment variables
if [ -z "$SONARR_URL" ]; then
    echo "ERROR: SONARR_URL environment variable is required"
    echo "Please set SONARR_URL to your Sonarr server URL (e.g., http://sonarr:8989)"
    exit 1
fi

if [ -z "$SONARR_API_KEY" ]; then
    echo "ERROR: SONARR_API_KEY environment variable is required"
    echo "Get your API key from Sonarr Settings > General > Security > API Key"
    exit 1
fi

# Check if API key looks valid (32 hex characters)
if ! echo "$SONARR_API_KEY" | grep -qE '^[a-fA-F0-9]{32}$'; then
    echo "WARNING: SONARR_API_KEY doesn't match typical Sonarr API key format"
    echo "Expected: 32 hexadecimal characters"
fi

# Show startup information
echo "🚀 Starting Sonarr Import Monitor v2.0.0"
echo "   Platform: $(uname -m)"
echo "   Python: $(python --version)"
echo "   Sonarr URL: $SONARR_URL"
echo "   Webhook Port: ${WEBHOOK_PORT:-8090}"
echo "   Log Level: ${LOG_LEVEL:-INFO}"
echo "   Log Format: ${LOG_FORMAT:-json}"

# Check if running with webhook
if [[ "$*" == *"--webhook"* ]] || [ "${WEBHOOK_ENABLED:-true}" = "true" ]; then
    echo "   Webhook Server: Enabled"
    if [ -n "$WEBHOOK_SECRET" ]; then
        echo "   Webhook Auth: Enabled (secret configured)"
    else
        echo "   Webhook Auth: Auto-generating secret"
    fi
else
    echo "   Webhook Server: Disabled"
fi

# Dry run mode check
if [[ "$*" == *"--dry-run"* ]] || [ "${DRY_RUN:-false}" = "true" ]; then
    echo "   🔸 DRY RUN MODE - No changes will be made"
fi

echo ""

# Change to app directory
cd /app

# Start the application with all passed arguments
echo "Starting application..."
python main.py "$@" &
PYTHON_PID=$!

# Wait for the Python process to finish
wait "$PYTHON_PID"