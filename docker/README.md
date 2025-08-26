# Sonarr Import Monitor - Docker Guide

This guide covers everything you need to run Sonarr Import Monitor in Docker containers with support for both ARM64 (M1/M2 Macs) and AMD64 (Intel/AMD) architectures.

## Quick Start

### Option 1: Simple Docker Compose (Recommended for beginners)

The easiest way to get started:

1. **Copy the environment template:**
   ```bash
   cp docker/.env.example .env
   ```

2. **Edit `.env` with your Sonarr details:**
   ```bash
   # Edit .env file
   SONARR_URL=http://your-sonarr:8989
   SONARR_API_KEY=your-32-character-api-key
   ```

3. **Start the container:**
   ```bash
   docker-compose -f docker/docker-compose.simple.yml up -d
   ```

4. **Configure Sonarr webhook:**
   - Check the logs for the auto-generated webhook secret
   - Add webhook in Sonarr Settings > Connect
   - URL: `http://your-server:8090/webhook/sonarr`
   - Header: `X-Webhook-Secret: <secret-from-logs>`

### Option 2: Docker Run (Single command)

```bash
docker run -d \
  --name sonarr-import-monitor \
  -e SONARR_URL=http://your-sonarr:8989 \
  -e SONARR_API_KEY=your-32-character-api-key \
  -p 8090:8090 \
  -v ./logs:/app/logs \
  sonarr-import-monitor:latest
```

### Option 3: Production Docker Compose (Advanced)

For production deployments with security hardening:

```bash
docker-compose -f docker/docker-compose.yml up -d
```

### Option 4: Run Script (For Development)

```bash
# Build and run locally
./docker/run.sh --build

# Development mode with live code reload
./docker/run.sh --dev

# One-time run with dry run mode
./docker/run.sh --once --dry-run
```

## Platform Support

| Platform | Architecture | Docker Support | Performance | Notes |
|----------|-------------|----------------|-------------|-------|
| **Apple M1/M2 Mac** | ARM64 | ✅ Native | Optimal | No emulation needed |
| **Intel Mac** | AMD64 | ✅ Native | Optimal | Standard x86_64 |
| **Ubuntu/Debian** | AMD64 | ✅ Native | Optimal | Most common server platform |
| **Raspberry Pi 4** | ARM64 | ✅ Native | Good | 64-bit OS required |
| **Synology NAS** | Varies | ✅ Native | Good | Both ARM64 and AMD64 models |
| **Windows WSL2** | AMD64 | ✅ Native | Good | Via Docker Desktop |

## Setup Comparison

| Setup Type | Use Case | Complexity | Security | Features |
|------------|----------|------------|----------|----------|
| **docker-compose.simple.yml** | First-time users, home labs | ⭐ Easy | Basic | Core monitoring |
| **docker run** | Quick testing, minimal setup | ⭐ Easy | Basic | Core monitoring |
| **docker-compose.yml** | Production deployments | ⭐⭐⭐ Advanced | High | Full security hardening |
| **run.sh scripts** | Development, custom builds | ⭐⭐ Medium | Medium | Development features |

### Recommendation:
- **New users**: Start with `docker-compose.simple.yml`
- **Production**: Use `docker-compose.yml` with proper security
- **Development**: Use `./docker/run.sh --dev`

## Configuration

### Environment Variables

#### Required
- `SONARR_URL` - Your Sonarr server URL (e.g., `http://sonarr:8989`)
- `SONARR_API_KEY` - 32-character API key from Sonarr Settings > General > Security

#### Webhook Settings
- `WEBHOOK_ENABLED=true` - Enable webhook server (default: true)
- `WEBHOOK_PORT=8090` - Webhook server port (default: 8090)
- `WEBHOOK_SECRET=your-secret` - Authentication secret (auto-generated if not set)
- `WEBHOOK_HOST=0.0.0.0` - Bind address (default: 0.0.0.0 for containers)
- `WEBHOOK_IMPORT_CHECK_DELAY=600` - Seconds to wait before checking imports

#### Monitoring Settings
- `MONITORING_INTERVAL=60` - Queue check interval in seconds
- `FORCE_IMPORT_THRESHOLD=10` - Score difference to trigger force import
- `REMOVE_PUBLIC_FAILURES=true` - Remove failed downloads from public trackers
- `PROTECT_PRIVATE_RATIO=true` - Protect private tracker ratios

#### Logging
- `LOG_LEVEL=INFO` - Logging level (DEBUG, INFO, WARNING, ERROR, CRITICAL)
- `LOG_FORMAT=json` - Log format (text for development, json for production)

#### Optional
- `DRY_RUN=false` - Enable dry run mode (no actual changes)

### Configuration File

You can also use a YAML configuration file, though environment variables are recommended for Docker deployments:

#### Using Config Files with Docker Compose

1. **Copy the example configuration**:
   ```bash
   cp docker/config.example.yaml config.yaml
   ```

2. **Edit the configuration file**:
   ```bash
   # Edit config.yaml with your settings
   nano config.yaml
   ```

3. **Uncomment the volume mount in docker-compose.yml**:
   ```yaml
   volumes:
     # Uncomment this line:
     - ./config.yaml:/app/config/config.yaml:ro
   ```

#### Using Config Files with Docker Run

Mount the config file directly:
```bash
docker run -d \
  --name sonarr-import-monitor \
  -v $(pwd)/config.yaml:/app/config/config.yaml:ro \
  -p 8090:8090 \
  sonarr-import-monitor:latest
```

#### Configuration Priority

Settings are loaded in this order (later overrides earlier):
1. Default values
2. Configuration file (`config.yaml`)
3. Environment variables (highest priority)

#### Example Configuration

See `docker/config.example.yaml` for a complete example with all available options:

```yaml
# Basic configuration
sonarr:
  url: "http://sonarr:8989"
  api_key: "your-32-character-api-key"

webhook:
  enabled: true
  port: 8090
  secret: "your-webhook-secret"

decisions:
  force_import_threshold: 10
  remove_public_failures: true
  protect_private_ratio: true

logging:
  level: "INFO"
  format: "json"
```

**Note**: For Docker deployments, environment variables are preferred as they're more secure and easier to manage.

## Networking

### Standalone Container

```bash
docker run -d \
  --name sonarr-import-monitor \
  --network bridge \
  -p 8090:8090 \
  -e SONARR_URL=http://192.168.1.100:8989 \
  -e SONARR_API_KEY=your-key \
  sonarr-import-monitor:latest
```

### Docker Compose with Sonarr

```yaml
version: '3.8'

services:
  sonarr:
    image: lscr.io/linuxserver/sonarr:latest
    container_name: sonarr
    ports:
      - "8989:8989"
    # ... other sonarr config

  sonarr-import-monitor:
    image: sonarr-import-monitor:latest
    container_name: sonarr-import-monitor
    depends_on:
      - sonarr
    environment:
      - SONARR_URL=http://sonarr:8989  # Use container name
      - SONARR_API_KEY=your-api-key
    ports:
      - "8090:8090"

networks:
  default:
    name: media-network
```

### Docker Network

If using a custom network:
```bash
# Create network
docker network create media-network

# Run containers on the network
docker run -d \
  --name sonarr-import-monitor \
  --network media-network \
  -e SONARR_URL=http://sonarr:8989 \
  sonarr-import-monitor:latest
```

## Building

### Local Build

```bash
# Build for current platform
docker build -f docker/Dockerfile -t sonarr-import-monitor:latest .

# Or use the build script
./docker/build.sh --load
```

### Multi-Platform Build

```bash
# Build for both ARM64 and AMD64
./docker/build.sh

# Build and push to registry
./docker/build.sh --push --registry ghcr.io/yourusername

# Build with latest tag
./docker/build.sh --push --latest
```

### Build Arguments

Available at build time:
- `PYTHON_VERSION=3.11` - Python version to use
- `BUILD_DATE` - Build timestamp (auto-set)
- `VERSION` - Version tag (from git)
- `COMMIT_SHA` - Git commit hash

## Security

### Container Security

The Docker image follows security best practices:

- **Non-root user**: Runs as `appuser` (UID 1000)
- **Read-only filesystem**: Root filesystem is read-only
- **Dropped capabilities**: Only essential capabilities retained
- **No shell**: Minimal attack surface
- **Security options**: `no-new-privileges:true`

### Secrets Management

**Never include secrets in the image!** Use environment variables:

```bash
# ❌ Don't do this
docker build --build-arg SONARR_API_KEY=secret .

# ✅ Do this instead
docker run -e SONARR_API_KEY=secret sonarr-import-monitor:latest
```

### Webhook Security

Generate a strong webhook secret:
```bash
# Generate 32-byte hex string
openssl rand -hex 32

# Or using Python
python -c "import secrets; print(secrets.token_hex(32))"
```

Configure in Sonarr:
- URL: `http://your-server:8090/webhook/sonarr`
- Method: `POST`
- Headers: `X-Webhook-Secret: your-generated-secret`

## Monitoring & Health Checks

### Health Check Endpoint

The container includes a built-in health check:
```bash
curl http://localhost:8090/health
```

Response:
```json
{
  "status": "healthy",
  "service": "Sonarr Import Monitor Webhook",
  "version": "2.0.0",
  "timestamp": "2025-01-26T10:30:00Z",
  "uptime_seconds": 3600,
  "cache_size": 5
}
```

### Metrics

Monitor performance via metrics endpoint:
```bash
curl http://localhost:8090/metrics
```

### Log Management

#### JSON Logs (Production)
```bash
docker logs sonarr-import-monitor | jq '.'
```

#### Text Logs (Development)
```bash
docker logs -f sonarr-import-monitor
```

#### Log Rotation
Configure in docker-compose.yml:
```yaml
logging:
  driver: "json-file"
  options:
    max-size: "10m"
    max-file: "3"
```

## Troubleshooting

### Common Issues

1. **Connection refused to Sonarr**
   ```bash
   # Check if Sonarr is accessible from container
   docker exec sonarr-import-monitor curl http://your-sonarr:8989
   
   # Use host network for testing
   docker run --network host -e SONARR_URL=http://localhost:8989 ...
   ```

2. **Authentication failed (401)**
   ```bash
   # Verify API key
   curl -H "X-Api-Key: your-key" http://your-sonarr:8989/api/v3/system/status
   ```

3. **Webhook not receiving events**
   ```bash
   # Test webhook endpoint
   curl http://localhost:8090/webhook/sonarr
   
   # Check Sonarr webhook configuration
   # Settings > Connect > Webhook > Test
   ```

4. **High memory usage**
   ```bash
   # Check container stats
   docker stats sonarr-import-monitor
   
   # Increase monitoring interval
   -e MONITORING_INTERVAL=120
   ```

### Debug Mode

Enable debug logging:
```bash
docker run \
  -e LOG_LEVEL=DEBUG \
  -e LOG_FORMAT=text \
  sonarr-import-monitor:latest --verbose
```

### Container Shell Access

For debugging (not available in production image):
```bash
# Use development image
docker run -it sonarr-import-monitor:latest sh

# Or exec into running container
docker exec -it sonarr-import-monitor sh
```

## Development

### Development Mode

```bash
# Run with source code mounted for live changes
./docker/run.sh --dev

# Or manually
docker run \
  -v $(pwd)/src:/app/src:ro \
  -v $(pwd)/main.py:/app/main.py:ro \
  -e LOG_LEVEL=DEBUG \
  -e LOG_FORMAT=text \
  sonarr-import-monitor:latest
```

### Building Development Image

```bash
# Build development image
docker build -f docker/Dockerfile --target builder -t sonarr-import-monitor:dev .

# Or use development compose
docker-compose -f docker/docker-compose.yml -f docker/docker-compose.dev.yml up
```

## Performance

### Resource Usage

Typical resource consumption:
- **Memory**: 128-256 MB
- **CPU**: <5% (single core)
- **Storage**: ~150 MB image size
- **Network**: Minimal (API calls only)

### Optimization

For high-load scenarios:
```bash
# Increase monitoring interval
-e MONITORING_INTERVAL=120

# Use JSON logging
-e LOG_FORMAT=json

# Reduce log level
-e LOG_LEVEL=WARNING
```

## Examples

### Home Lab Setup

```yaml
version: '3.8'
services:
  sonarr-import-monitor:
    image: sonarr-import-monitor:latest
    container_name: sonarr-import-monitor
    environment:
      - SONARR_URL=http://192.168.1.100:8989
      - SONARR_API_KEY=${SONARR_API_KEY}
      - WEBHOOK_SECRET=${WEBHOOK_SECRET}
      - LOG_FORMAT=text
    ports:
      - "8090:8090"
    volumes:
      - ./logs:/app/logs
    restart: unless-stopped
```

### Production Deployment

```yaml
version: '3.8'
services:
  sonarr-import-monitor:
    image: ghcr.io/yourusername/sonarr-import-monitor:latest
    container_name: sonarr-import-monitor
    user: "1000:1000"
    read_only: true
    security_opt:
      - no-new-privileges:true
    environment:
      - SONARR_URL=${SONARR_URL}
      - SONARR_API_KEY=${SONARR_API_KEY}
      - WEBHOOK_SECRET=${WEBHOOK_SECRET}
      - LOG_FORMAT=json
      - LOG_LEVEL=INFO
    ports:
      - "8090:8090"
    volumes:
      - /var/log/sonarr-import-monitor:/app/logs
      - /tmp
    deploy:
      resources:
        limits:
          memory: 256M
          cpus: '0.5'
    logging:
      driver: "json-file"
      options:
        max-size: "10m"
        max-file: "3"
    restart: unless-stopped
    healthcheck:
      test: ["CMD", "curl", "-f", "http://localhost:8090/health"]
      interval: 30s
      timeout: 5s
      retries: 3
```

### CI/CD Pipeline

```yaml
version: '3.8'
services:
  test:
    build:
      context: .
      dockerfile: docker/Dockerfile
    environment:
      - SONARR_URL=http://test-sonarr:8989
      - SONARR_API_KEY=test-key
      - DRY_RUN=true
    command: ["--test-config"]
```

---

For more information, see the main [README.md](../README.md) and [IMPROVEMENT_PLAN.md](../IMPROVEMENT_PLAN.md).