#!/bin/bash

# Local run script for Sonarr Import Monitor Docker container
# Provides easy testing and development workflow

set -e

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Configuration
IMAGE_NAME="sonarr-import-monitor"
CONTAINER_NAME="sonarr-import-monitor"
DEFAULT_PORT="8090"

# Default environment file
ENV_FILE="docker/.env"

echo -e "${BLUE}🐳 Sonarr Import Monitor - Docker Run Script${NC}"
echo -e "${BLUE}=============================================${NC}"

# Parse command line options
MODE="webhook"
PORT="$DEFAULT_PORT"
ENV_FILE_PROVIDED=false
BUILD=false
LOGS=false
STOP=false
REMOVE=false
DEV=false
DRY_RUN=false

usage() {
    echo "Usage: $0 [OPTIONS]"
    echo ""
    echo "Options:"
    echo "  --build              Build image before running"
    echo "  --dev                Run in development mode with source code mounted"
    echo "  --once               Run once and exit (polling mode)"
    echo "  --webhook            Run with webhook server (default)"
    echo "  --dry-run            Enable dry run mode (no actual changes)"
    echo "  --port PORT          Webhook server port (default: 8090)"
    echo "  --env-file FILE      Environment file to use (default: docker/.env)"
    echo "  --logs               Show container logs and exit"
    echo "  --stop               Stop running container"
    echo "  --remove             Remove container"
    echo "  --help, -h           Show this help"
    echo ""
    echo "Examples:"
    echo "  $0                          # Run with webhook server"
    echo "  $0 --build                  # Build and run"
    echo "  $0 --dev                    # Development mode with live code"
    echo "  $0 --once --dry-run         # Single run in dry run mode"
    echo "  $0 --logs                   # Show logs"
    echo "  $0 --stop                   # Stop container"
}

while [[ $# -gt 0 ]]; do
    case $1 in
        --build)
            BUILD=true
            shift
            ;;
        --dev)
            DEV=true
            shift
            ;;
        --once)
            MODE="once"
            shift
            ;;
        --webhook)
            MODE="webhook"
            shift
            ;;
        --dry-run)
            DRY_RUN=true
            shift
            ;;
        --port)
            PORT="$2"
            shift 2
            ;;
        --env-file)
            ENV_FILE="$2"
            ENV_FILE_PROVIDED=true
            shift 2
            ;;
        --logs)
            LOGS=true
            shift
            ;;
        --stop)
            STOP=true
            shift
            ;;
        --remove)
            REMOVE=true
            shift
            ;;
        --help|-h)
            usage
            exit 0
            ;;
        *)
            echo -e "${RED}Unknown option: $1${NC}"
            usage
            exit 1
            ;;
    esac
done

# Handle stop command
if [[ "$STOP" == "true" ]]; then
    echo -e "${YELLOW}🛑 Stopping container...${NC}"
    docker stop $CONTAINER_NAME 2>/dev/null || echo -e "${YELLOW}Container not running${NC}"
    exit 0
fi

# Handle remove command
if [[ "$REMOVE" == "true" ]]; then
    echo -e "${YELLOW}🗑️ Removing container...${NC}"
    docker rm $CONTAINER_NAME 2>/dev/null || echo -e "${YELLOW}Container not found${NC}"
    exit 0
fi

# Handle logs command
if [[ "$LOGS" == "true" ]]; then
    echo -e "${BLUE}📋 Showing container logs...${NC}"
    docker logs -f $CONTAINER_NAME
    exit 0
fi

# Check if Docker is running
if ! docker info &> /dev/null; then
    echo -e "${RED}❌ Docker is not running or not accessible${NC}"
    exit 1
fi

# Check environment file
if [[ ! -f "$ENV_FILE" ]]; then
    if [[ "$ENV_FILE_PROVIDED" == "true" ]]; then
        echo -e "${RED}❌ Environment file not found: $ENV_FILE${NC}"
        exit 1
    else
        echo -e "${YELLOW}⚠️ Environment file not found: $ENV_FILE${NC}"
        echo -e "${YELLOW}💡 Creating from template...${NC}"
        
        # Create .env from template
        cp docker/.env.docker "$ENV_FILE"
        
        echo -e "${YELLOW}📝 Please edit $ENV_FILE with your Sonarr details:${NC}"
        echo -e "  - SONARR_URL=http://your-sonarr:8989"
        echo -e "  - SONARR_API_KEY=your-32-character-api-key"
        echo ""
        echo -e "${BLUE}Run this script again after updating the environment file.${NC}"
        exit 1
    fi
fi

# Build image if requested
if [[ "$BUILD" == "true" ]]; then
    echo -e "${BLUE}🏗️ Building Docker image...${NC}"
    ./docker/build.sh --load
    echo ""
fi

# Check if image exists
if ! docker image inspect $IMAGE_NAME:latest &> /dev/null; then
    echo -e "${YELLOW}⚠️ Docker image not found: $IMAGE_NAME:latest${NC}"
    echo -e "${BLUE}🏗️ Building image...${NC}"
    ./docker/build.sh --load
    echo ""
fi

# Stop existing container if running
if docker ps -q -f name=$CONTAINER_NAME | grep -q .; then
    echo -e "${YELLOW}🛑 Stopping existing container...${NC}"
    docker stop $CONTAINER_NAME
fi

# Remove existing container if exists
if docker ps -aq -f name=$CONTAINER_NAME | grep -q .; then
    echo -e "${YELLOW}🗑️ Removing existing container...${NC}"
    docker rm $CONTAINER_NAME
fi

# Prepare Docker run command
RUN_CMD="docker run"
RUN_CMD="$RUN_CMD --name $CONTAINER_NAME"
RUN_CMD="$RUN_CMD --env-file $ENV_FILE"
RUN_CMD="$RUN_CMD -p $PORT:8090"

# Add dry run environment if requested
if [[ "$DRY_RUN" == "true" ]]; then
    RUN_CMD="$RUN_CMD -e DRY_RUN=true"
fi

# Development mode
if [[ "$DEV" == "true" ]]; then
    echo -e "${BLUE}🔧 Running in development mode...${NC}"
    RUN_CMD="$RUN_CMD -v $(pwd)/src:/app/src:ro"
    RUN_CMD="$RUN_CMD -v $(pwd)/main.py:/app/main.py:ro"
    RUN_CMD="$RUN_CMD -v $(pwd)/config:/app/config:ro"
    RUN_CMD="$RUN_CMD -e LOG_LEVEL=DEBUG"
    RUN_CMD="$RUN_CMD -e LOG_FORMAT=text"
    MODE="webhook"  # Force webhook mode for development
else
    # Production mode - mount logs directory
    RUN_CMD="$RUN_CMD -v $(pwd)/docker/logs:/app/logs"
fi

# Set command based on mode
if [[ "$MODE" == "once" ]]; then
    RUN_CMD="$RUN_CMD --rm"  # Remove container after run
    CMD_ARGS="--once"
else
    RUN_CMD="$RUN_CMD -d"  # Run in background
    CMD_ARGS="--webhook"
fi

# Add dry run flag to command if requested
if [[ "$DRY_RUN" == "true" ]]; then
    CMD_ARGS="$CMD_ARGS --dry-run"
fi

# Final command
RUN_CMD="$RUN_CMD $IMAGE_NAME:latest $CMD_ARGS"

echo -e "${BLUE}🚀 Starting Sonarr Import Monitor...${NC}"
echo -e "Mode: ${GREEN}$MODE${NC}"
echo -e "Port: ${GREEN}$PORT${NC}"
echo -e "Dry Run: ${GREEN}$([ "$DRY_RUN" == "true" ] && echo "Yes" || echo "No")${NC}"
echo -e "Development: ${GREEN}$([ "$DEV" == "true" ] && echo "Yes" || echo "No")${NC}"
echo ""

# Show Docker run command
echo -e "${BLUE}🔨 Docker Command:${NC}"
echo "$RUN_CMD"
echo ""

# Execute run command
if eval $RUN_CMD; then
    if [[ "$MODE" == "once" ]]; then
        echo -e "${GREEN}✅ Single run completed${NC}"
    else
        echo -e "${GREEN}✅ Container started successfully!${NC}"
        
        # Wait a moment then show initial logs
        sleep 2
        echo ""
        echo -e "${BLUE}📋 Initial logs:${NC}"
        docker logs $CONTAINER_NAME
        
        echo ""
        echo -e "${BLUE}🌐 Access points:${NC}"
        echo -e "  Health Check: ${GREEN}http://localhost:$PORT/health${NC}"
        echo -e "  Webhook URL:  ${GREEN}http://localhost:$PORT/webhook/sonarr${NC}"
        echo -e "  Metrics:      ${GREEN}http://localhost:$PORT/metrics${NC}"
        
        echo ""
        echo -e "${BLUE}📋 Useful commands:${NC}"
        echo -e "  View logs:    ${GREEN}docker logs -f $CONTAINER_NAME${NC}"
        echo -e "  Stop:         ${GREEN}./docker/run.sh --stop${NC}"
        echo -e "  Remove:       ${GREEN}./docker/run.sh --remove${NC}"
        echo -e "  Or use:       ${GREEN}docker stop $CONTAINER_NAME && docker rm $CONTAINER_NAME${NC}"
    fi
else
    echo ""
    echo -e "${RED}❌ Failed to start container!${NC}"
    exit 1
fi