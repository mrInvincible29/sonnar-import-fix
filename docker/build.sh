#!/bin/bash

# Multi-architecture build script for Sonarr Import Monitor
# Builds for both ARM64 (M1 Mac, Raspberry Pi) and AMD64 (Intel/AMD)

set -e

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Configuration
IMAGE_NAME="sonarr-import-monitor"
REGISTRY="ghcr.io/yourusername"  # Change this to your registry
PLATFORMS="linux/amd64,linux/arm64"

# Get version from git or default
if git rev-parse --git-dir > /dev/null 2>&1; then
    VERSION=$(git describe --tags --always --dirty)
    BRANCH=$(git rev-parse --abbrev-ref HEAD)
else
    VERSION="latest"
    BRANCH="unknown"
fi

BUILD_DATE=$(date -u +'%Y-%m-%dT%H:%M:%SZ')
COMMIT_SHA=$(git rev-parse --short HEAD 2>/dev/null || echo "unknown")

echo -e "${BLUE}🏗️  Sonarr Import Monitor - Multi-Platform Build${NC}"
echo -e "${BLUE}=================================================${NC}"
echo -e "Image: ${GREEN}${IMAGE_NAME}${NC}"
echo -e "Version: ${GREEN}${VERSION}${NC}"
echo -e "Branch: ${GREEN}${BRANCH}${NC}"
echo -e "Platforms: ${GREEN}${PLATFORMS}${NC}"
echo -e "Build Date: ${GREEN}${BUILD_DATE}${NC}"
echo -e "Commit: ${GREEN}${COMMIT_SHA}${NC}"
echo ""

# Parse command line options
PUSH=false
REGISTRY_PROVIDED=false
LOAD=false
LATEST=false

while [[ $# -gt 0 ]]; do
    case $1 in
        --push)
            PUSH=true
            shift
            ;;
        --registry)
            REGISTRY="$2"
            REGISTRY_PROVIDED=true
            shift 2
            ;;
        --load)
            LOAD=true
            # When loading, we can only build for current platform
            PLATFORMS="$(docker info --format '{{.Architecture}}')"
            if [[ "$PLATFORMS" == "x86_64" ]]; then
                PLATFORMS="linux/amd64"
            elif [[ "$PLATFORMS" == "aarch64" ]]; then
                PLATFORMS="linux/arm64"
            fi
            shift
            ;;
        --latest)
            LATEST=true
            shift
            ;;
        --help|-h)
            echo "Usage: $0 [OPTIONS]"
            echo ""
            echo "Options:"
            echo "  --push                Push images to registry"
            echo "  --registry REGISTRY   Registry to push to (default: ghcr.io/yourusername)"
            echo "  --load               Load image to local Docker (single platform only)"
            echo "  --latest             Also tag as 'latest'"
            echo "  --help, -h           Show this help"
            echo ""
            echo "Examples:"
            echo "  $0                          # Build for local testing"
            echo "  $0 --load                  # Build and load to local Docker"
            echo "  $0 --push --latest          # Build and push with 'latest' tag"
            echo "  $0 --registry my.registry.com/myuser --push"
            exit 0
            ;;
        *)
            echo -e "${RED}Unknown option: $1${NC}"
            exit 1
            ;;
    esac
done

# Check if Docker is running
if ! docker info &> /dev/null; then
    echo -e "${RED}❌ Docker is not running or not accessible${NC}"
    exit 1
fi

# Enable Docker BuildKit
export DOCKER_BUILDKIT=1

# Check if buildx is available
if ! docker buildx version &> /dev/null; then
    echo -e "${RED}❌ Docker buildx is not available${NC}"
    echo -e "${YELLOW}💡 Please install Docker BuildKit and buildx${NC}"
    exit 1
fi

# Create buildx builder if it doesn't exist
BUILDER_NAME="multiarch-builder"
if ! docker buildx inspect $BUILDER_NAME &> /dev/null; then
    echo -e "${YELLOW}🔧 Creating buildx builder for multi-platform builds...${NC}"
    docker buildx create --name $BUILDER_NAME --driver docker-container --use
    docker buildx inspect --bootstrap
else
    echo -e "${GREEN}✅ Using existing buildx builder: $BUILDER_NAME${NC}"
    docker buildx use $BUILDER_NAME
fi

# Build tags
TAGS=""
if [[ "$REGISTRY_PROVIDED" == "true" ]]; then
    TAGS="$TAGS -t $REGISTRY/$IMAGE_NAME:$VERSION"
    if [[ "$LATEST" == "true" ]]; then
        TAGS="$TAGS -t $REGISTRY/$IMAGE_NAME:latest"
    fi
else
    TAGS="$TAGS -t $IMAGE_NAME:$VERSION"
    if [[ "$LATEST" == "true" ]]; then
        TAGS="$TAGS -t $IMAGE_NAME:latest"
    fi
fi

# Build arguments
BUILD_ARGS=""
BUILD_ARGS="$BUILD_ARGS --build-arg BUILD_DATE=$BUILD_DATE"
BUILD_ARGS="$BUILD_ARGS --build-arg VERSION=$VERSION"
BUILD_ARGS="$BUILD_ARGS --build-arg COMMIT_SHA=$COMMIT_SHA"

# Build command
BUILD_CMD="docker buildx build"
BUILD_CMD="$BUILD_CMD --platform $PLATFORMS"
BUILD_CMD="$BUILD_CMD $BUILD_ARGS"
BUILD_CMD="$BUILD_CMD $TAGS"
BUILD_CMD="$BUILD_CMD -f docker/Dockerfile"

if [[ "$PUSH" == "true" ]]; then
    BUILD_CMD="$BUILD_CMD --push"
    echo -e "${YELLOW}📤 Will push to registry${NC}"
elif [[ "$LOAD" == "true" ]]; then
    BUILD_CMD="$BUILD_CMD --load"
    echo -e "${YELLOW}📥 Will load to local Docker${NC}"
fi

BUILD_CMD="$BUILD_CMD ."

# Show build command
echo -e "${BLUE}🔨 Build Command:${NC}"
echo "$BUILD_CMD"
echo ""

# Confirm before building if pushing
if [[ "$PUSH" == "true" ]]; then
    echo -e "${YELLOW}⚠️  This will build and push images to the registry.${NC}"
    read -p "Continue? (y/N) " -n 1 -r
    echo
    if [[ ! $REPLY =~ ^[Yy]$ ]]; then
        echo -e "${YELLOW}Build cancelled${NC}"
        exit 0
    fi
fi

# Execute build
echo -e "${BLUE}🚀 Starting build...${NC}"
echo ""

if eval $BUILD_CMD; then
    echo ""
    echo -e "${GREEN}✅ Build completed successfully!${NC}"
    
    if [[ "$LOAD" == "true" ]]; then
        echo -e "${GREEN}📥 Image loaded to local Docker${NC}"
        echo ""
        echo -e "${BLUE}🏃 Run with:${NC}"
        echo "docker run -e SONARR_URL=http://your-sonarr:8989 -e SONARR_API_KEY=your-api-key -p 8090:8090 $IMAGE_NAME:$VERSION"
    fi
    
    if [[ "$PUSH" == "true" ]]; then
        echo -e "${GREEN}📤 Images pushed to registry${NC}"
        echo ""
        echo -e "${BLUE}🌍 Available platforms:${NC}"
        IFS=',' read -ra PLATFORM_ARRAY <<< "$PLATFORMS"
        for platform in "${PLATFORM_ARRAY[@]}"; do
            echo "  - $platform"
        done
        echo ""
        echo -e "${BLUE}🏃 Run with:${NC}"
        if [[ "$REGISTRY_PROVIDED" == "true" ]]; then
            echo "docker run -e SONARR_URL=http://your-sonarr:8989 -e SONARR_API_KEY=your-api-key -p 8090:8090 $REGISTRY/$IMAGE_NAME:$VERSION"
        else
            echo "docker run -e SONARR_URL=http://your-sonarr:8989 -e SONARR_API_KEY=your-api-key -p 8090:8090 $IMAGE_NAME:$VERSION"
        fi
    fi
    
    echo ""
    echo -e "${BLUE}📋 Image Information:${NC}"
    echo -e "  Name: $IMAGE_NAME"
    echo -e "  Version: $VERSION"
    echo -e "  Build Date: $BUILD_DATE"
    echo -e "  Commit: $COMMIT_SHA"
    echo -e "  Platforms: $PLATFORMS"
    
else
    echo ""
    echo -e "${RED}❌ Build failed!${NC}"
    exit 1
fi