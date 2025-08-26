# Sonarr Import Monitor - Detailed Improvement Plan

## Overview
Transform the current monolithic script into a production-ready, dockerized application with proper code organization, testing, and security.

## Current State Analysis
- **Single file**: 1142 lines in `sonarr_import_monitor.py`
- **Security issues**: Hardcoded API keys in code
- **No tests**: Zero test coverage
- **Platform limitations**: Requires manual Python setup
- **Webhook vulnerability**: No authentication on webhook endpoint

---

## Phase 1: Code Organization & Security (Week 1)

### 1.1 Project Structure
```
sonarr-import-monitor/
├── src/
│   ├── __init__.py
│   ├── core/
│   │   ├── __init__.py
│   │   ├── monitor.py          # Core monitoring logic (400 lines)
│   │   └── analyzer.py         # Score analysis logic (200 lines)
│   ├── api/
│   │   ├── __init__.py
│   │   ├── sonarr_client.py   # Sonarr API wrapper (300 lines)
│   │   └── webhook_server.py   # Flask webhook server (250 lines)
│   ├── config/
│   │   ├── __init__.py
│   │   ├── loader.py           # Configuration loader
│   │   └── validator.py        # Config validation
│   └── utils/
│       ├── __init__.py
│       ├── logger.py           # Logging setup
│       └── decorators.py       # Retry, rate limiting
├── tests/
├── docker/
├── config/
│   ├── config.example.yaml
│   └── .env.example
├── main.py
└── requirements.txt
```

### 1.2 Configuration Security Implementation

#### Config Loader with Environment Variable Override
```python
# src/config/loader.py
import os
import yaml
from typing import Dict, Any
from pathlib import Path

class ConfigLoader:
    """Secure configuration loader with environment variable override"""
    
    SENSITIVE_KEYS = ['api_key', 'webhook_secret', 'password']
    
    def __init__(self, config_path: str = None):
        self.config = self._load_base_config(config_path)
        self._override_with_env()
        self._validate_config()
        self._mask_sensitive_values()
    
    def _load_base_config(self, config_path: str) -> Dict[str, Any]:
        """Load base configuration from YAML file"""
        if config_path and Path(config_path).exists():
            with open(config_path, 'r') as f:
                return yaml.safe_load(f)
        return {}
    
    def _override_with_env(self):
        """Override config values with environment variables"""
        env_mappings = {
            'SONARR_URL': 'sonarr.url',
            'SONARR_API_KEY': 'sonarr.api_key',
            'WEBHOOK_PORT': 'webhook.port',
            'WEBHOOK_SECRET': 'webhook.secret',
            'FORCE_IMPORT_THRESHOLD': 'decisions.force_import_threshold',
            'LOG_LEVEL': 'logging.level'
        }
        
        for env_key, config_path in env_mappings.items():
            value = os.getenv(env_key)
            if value:
                self._set_nested_value(config_path, value)
    
    def _validate_config(self):
        """Validate required configuration values"""
        required = [
            'sonarr.url',
            'sonarr.api_key'
        ]
        
        for key_path in required:
            if not self._get_nested_value(key_path):
                raise ValueError(f"Required configuration missing: {key_path}")
    
    def _mask_sensitive_values(self):
        """Mask sensitive values in logs"""
        self.masked_config = self._deep_copy_and_mask(self.config)
    
    def _deep_copy_and_mask(self, obj):
        """Recursively mask sensitive values"""
        if isinstance(obj, dict):
            result = {}
            for key, value in obj.items():
                if any(sensitive in key.lower() for sensitive in self.SENSITIVE_KEYS):
                    result[key] = "***MASKED***" if value else None
                else:
                    result[key] = self._deep_copy_and_mask(value)
            return result
        return obj
```

### 1.3 Webhook Security Implementation

#### Authenticated Webhook Server
```python
# src/api/webhook_server.py
from flask genius Flask, request, jsonify
import hmac
import hashlib
from functools import wraps
from datetime import datetime, timedelta
from collections import defaultdict
import time

class SecureWebhookServer:
    """Webhook server with authentication and rate limiting"""
    
    def __init__(self, monitor, config):
        self.monitor = monitor
        self.config = config
        self.app = Flask(__name__)
        self.webhook_secret = config.get('webhook.secret')
        
        # Rate limiting
        self.request_counts = defaultdict(list)
        self.max_requests_per_minute = 30
        
        self.setup_routes()
    
    def verify_webhook_signature(self, f):
        """Decorator to verify webhook signatures"""
        @wraps(f)
        def decorated_function(*args, **kwargs):
            # Option 1: Shared secret in header
            if self.webhook_secret:
                provided_secret = request.headers.get('X-Webhook-Secret')
                if not provided_secret:
                    return jsonify({'error': 'Missing webhook secret'}), 401
                
                if not hmac.compare_digest(provided_secret, self.webhook_secret):
                    return jsonify({'error': 'Invalid webhook secret'}), 401
            
            # Option 2: HMAC signature verification
            signature = request.headers.get('X-Webhook-Signature')
            if signature:
                expected_sig = hmac.new(
                    self.webhook_secret.encode(),
                    request.data,
                    hashlib.sha256
                ).hexdigest()
                
                if not hmac.compare_digest(signature, f"sha256={expected_sig}"):
                    return jsonify({'error': 'Invalid signature'}), 401
            
            return f(*args, **kwargs)
        return decorated_function
    
    def rate_limit(self, f):
        """Decorator for rate limiting"""
        @wraps(f)
        def decorated_function(*args, **kwargs):
            # Get client identifier (IP or API key)
            client_id = request.remote_addr
            
            # Clean old entries
            current_time = time.time()
            self.request_counts[client_id] = [
                req_time for req_time in self.request_counts[client_id]
                if current_time - req_time < 60
            ]
            
            # Check rate limit
            if len(self.request_counts[client_id]) >= self.max_requests_per_minute:
                return jsonify({'error': 'Rate limit exceeded'}), 429
            
            # Record request
            self.request_counts[client_id].append(current_time)
            
            return f(*args, **kwargs)
        return decorated_function
    
    def setup_routes(self):
        """Setup webhook routes with security"""
        
        @self.app.route('/health')
        def health():
            """Public health check endpoint"""
            return jsonify({
                'status': 'healthy',
                'timestamp': datetime.now().isoformat(),
                'version': '2.0.0'
            })
        
        @self.app.route('/webhook/sonarr', methods=['POST'])
        @self.rate_limit
        @self.verify_webhook_signature
        def webhook_handler():
            """Secured webhook endpoint"""
            try:
                data = request.json
                event_type = data.get('eventType')
                
                # Log webhook receipt
                self.log_webhook_event(event_type, data)
                
                # Process event
                result = self.process_event(event_type, data)
                return jsonify(result), 200
                
            except Exception as e:
                logger.error(f"Webhook processing error: {e}")
                return jsonify({'error': 'Internal error'}), 500
```

---

## Phase 2: Docker Multi-Platform Support (Week 1-2)

### 2.1 Multi-Architecture Dockerfile

#### Production Dockerfile with Multi-Platform Support
```dockerfile
# docker/Dockerfile
# This Dockerfile supports both ARM64 (M1 Mac) and AMD64 (Intel/AMD)
FROM --platform=$BUILDPLATFORM python:3.11-slim AS builder

# Build arguments for cross-compilation
ARG TARGETPLATFORM
ARG BUILDPLATFORM
RUN echo "Building on $BUILDPLATFORM for $TARGETPLATFORM"

# Install build dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    python3-dev \
    && rm -rf /var/lib/apt/lists/*

# Install Python dependencies
WORKDIR /build
COPY requirements.txt .
RUN pip install --no-cache-dir --user -r requirements.txt

# Runtime stage - minimal image
FROM python:3.11-slim

# Install runtime dependencies (if needed)
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Create non-root user
RUN groupadd -r appuser && useradd -r -g appuser -u 1000 appuser

# Copy Python packages from builder
COPY --from=builder /root/.local /home/appuser/.local

# Set up application
WORKDIR /app
COPY --chown=appuser:appuser src/ ./src/
COPY --chown=appuser:appuser main.py .

# Switch to non-root user
USER appuser

# Add Python packages to PATH
ENV PATH=/home/appuser/.local/bin:$PATH

# Health check
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD curl -f http://localhost:${WEBHOOK_PORT:-8090}/health || exit 1

# Default environment variables
ENV PYTHONUNBUFFERED=1 \
    LOG_LEVEL=INFO \
    WEBHOOK_PORT=8090

# Expose webhook port
EXPOSE 8090

# Run application
ENTRYPOINT ["python", "-u"]
CMD ["main.py"]
```

### 2.2 Building for Multiple Platforms

#### Build Script for Multi-Architecture
```bash
#!/bin/bash
# docker/build.sh

# Enable Docker BuildKit
export DOCKER_BUILDKIT=1

# Create builder if it doesn't exist
docker buildx create --name multiarch --driver docker-container --use 2>/dev/null || true

# Build for multiple platforms
docker buildx build \
    --platform linux/amd64,linux/arm64 \
    --tag sonarr-import-monitor:latest \
    --tag sonarr-import-monitor:$(git describe --tags --always) \
    --build-arg BUILD_DATE=$(date -u +'%Y-%m-%dT%H:%M:%SZ') \
    --build-arg VERSION=$(git describe --tags --always) \
    --push \
    -f docker/Dockerfile \
    .

echo "✅ Multi-architecture image built and pushed"
echo "Supported platforms:"
echo "  - linux/amd64 (Intel/AMD processors - Debian, Ubuntu, etc.)"
echo "  - linux/arm64 (Apple M1/M2, Raspberry Pi 4, etc.)"
```

### 2.3 Docker Compose Configuration

#### docker-compose.yml with Platform Support
```yaml
# docker/docker-compose.yml
version: '3.8'

services:
  sonarr-import-monitor:
    image: sonarr-import-monitor:latest
    build:
      context: ..
      dockerfile: docker/Dockerfile
      platforms:
        - linux/amd64
        - linux/arm64
    container_name: sonarr-import-monitor
    
    # Security: Read-only root filesystem
    read_only: true
    
    # Security: Drop unnecessary capabilities
    cap_drop:
      - ALL
    cap_add:
      - NET_BIND_SERVICE
    
    environment:
      # Required settings (override in .env file)
      - SONARR_URL=${SONARR_URL:?Please set SONARR_URL}
      - SONARR_API_KEY=${SONARR_API_KEY:?Please set SONARR_API_KEY}
      
      # Webhook settings
      - WEBHOOK_ENABLED=${WEBHOOK_ENABLED:-true}
      - WEBHOOK_PORT=${WEBHOOK_PORT:-8090}
      - WEBHOOK_SECRET=${WEBHOOK_SECRET:-}  # Optional but recommended
      
      # Monitoring settings
      - CHECK_INTERVAL=${CHECK_INTERVAL:-60}
      - FORCE_IMPORT_THRESHOLD=${FORCE_IMPORT_THRESHOLD:-10}
      
      # Logging
      - LOG_LEVEL=${LOG_LEVEL:-INFO}
      - LOG_FORMAT=${LOG_FORMAT:-json}  # json or text
    
    ports:
      - "${WEBHOOK_PORT:-8090}:8090"
    
    volumes:
      # Config file (optional, env vars take precedence)
      - ./config/config.yaml:/app/config/config.yaml:ro
      
      # Logs (with write access)
      - ./logs:/app/logs
      
      # Temp directory for Flask
      - /tmp
    
    restart: unless-stopped
    
    healthcheck:
      test: ["CMD", "curl", "-f", "http://localhost:8090/health"]
      interval: 30s
      timeout: 5s
      retries: 3
      start_period: 10s
    
    # Resource limits
    deploy:
      resources:
        limits:
          cpus: '1'
          memory: 512M
        reservations:
          cpus: '0.25'
          memory: 128M

# Optional: Add network for Sonarr integration
networks:
  default:
    name: media-network
    external: true
```

### 2.4 Platform Compatibility Matrix

| Platform | Architecture | Docker Support | Notes |
|----------|-------------|----------------|-------|
| **Apple M1/M2 Mac** | ARM64 | ✅ Native | Runs natively, no emulation needed |
| **Intel Mac** | AMD64 | ✅ Native | Standard x86_64 support |
| **Ubuntu Server** | AMD64 | ✅ Native | Most common server platform |
| **Debian** | AMD64 | ✅ Native | Stable server platform |
| **Raspberry Pi 4** | ARM64 | ✅ Native | 64-bit OS required |
| **Synology NAS** | AMD64/ARM64 | ✅ Native | Depends on model |
| **Windows WSL2** | AMD64 | ✅ Native | Through Docker Desktop |

---

## Phase 3: Testing Implementation (Week 2)

### 3.1 Unit Test Structure

#### Test Configuration
```python
# tests/conftest.py
import pytest
from unittest.mock import Mock, MagicMock
import yaml

@pytest.fixture
def mock_config():
    """Provide test configuration"""
    return {
        'sonarr': {
            'url': 'http://test-sonarr:8989',
            'api_key': 'test-key-123'
        },
        'webhook': {
            'secret': 'test-secret',
            'port': 8090
        },
        'decisions': {
            'force_import_threshold': 10
        },
        'trackers': {
            'private': ['privatehd', 'beyondhd'],
            'public': ['nyaa', 'rarbg']
        }
    }

@pytest.fixture
def mock_sonarr_api():
    """Mock Sonarr API responses"""
    mock = MagicMock()
    mock.get_queue.return_value = [
        {
            'id': 1,
            'episode': {'id': 100},
            'downloadId': 'abc123',
            'status': 'completed',
            'trackedDownloadState': 'importPending'
        }
    ]
    return mock
```

#### Core Logic Tests
```python
# tests/test_analyzer.py
import pytest
from src.core.analyzer import ScoreAnalyzer

class TestScoreAnalyzer:
    """Test score analysis logic"""
    
    def test_force_import_decision(self):
        """Test force import is triggered on score difference"""
        analyzer = ScoreAnalyzer(threshold=10)
        
        decision = analyzer.analyze(
            grab_score=100,
            current_score=80,
            is_private_tracker=False
        )
        
        assert decision.action == "force_import"
        assert decision.reason == "Grab score 20 points higher"
    
    def test_private_tracker_protection(self):
        """Test private tracker downloads are protected"""
        analyzer = ScoreAnalyzer(threshold=10)
        
        decision = analyzer.analyze(
            grab_score=80,
            current_score=100,
            is_private_tracker=True
        )
        
        assert decision.action == "keep"
        assert "private tracker" in decision.reason.lower()
    
    def test_public_tracker_removal(self):
        """Test public tracker downloads are removed when score is lower"""
        analyzer = ScoreAnalyzer(threshold=10)
        
        decision = analyzer.analyze(
            grab_score=80,
            current_score=100,
            is_private_tracker=False
        )
        
        assert decision.action == "remove"
        assert "public tracker" in decision.reason.lower()
```

#### Integration Tests
```python
# tests/test_integration.py
import pytest
import requests_mock
from src.api.sonarr_client import SonarrClient

class TestSonarrIntegration:
    """Test Sonarr API integration"""
    
    def test_queue_processing(self, mock_config):
        """Test end-to-end queue processing"""
        with requests_mock.Mocker() as m:
            # Mock Sonarr endpoints
            m.get('/api/v3/queue', json={'records': [...]})
            m.get('/api/v3/history', json={'records': [...]})
            m.put('/api/v3/manualimport', json={'success': True})
            
            client = SonarrClient(mock_config)
            result = client.process_queue()
            
            assert result.processed == 1
            assert result.forced_imports == 1
```

### 3.2 Test Execution

#### GitHub Actions CI/CD
```yaml
# .github/workflows/test.yml
name: Test and Build

on:
  push:
    branches: [main, develop]
  pull_request:
    branches: [main]

jobs:
  test:
    runs-on: ubuntu-latest
    strategy:
      matrix:
        python-version: ['3.9', '3.10', '3.11']
    
    steps:
    - uses: actions/checkout@v3
    
    - name: Set up Python
      uses: actions/setup-python@v4
      with:
        python-version: ${{ matrix.python-version }}
    
    - name: Install dependencies
      run: |
        pip install -r requirements.txt
        pip install -r requirements-dev.txt
    
    - name: Run tests
      run: |
        pytest tests/ -v --cov=src --cov-report=xml
    
    - name: Upload coverage
      uses: codecov/codecov-action@v3
  
  docker-build:
    needs: test
    runs-on: ubuntu-latest
    
    steps:
    - uses: actions/checkout@v3
    
    - name: Set up QEMU
      uses: docker/setup-qemu-action@v2
    
    - name: Set up Docker Buildx
      uses: docker/setup-buildx-action@v2
    
    - name: Build multi-platform image
      run: |
        docker buildx build \
          --platform linux/amd64,linux/arm64 \
          --tag sonarr-import-monitor:test \
          -f docker/Dockerfile \
          .
```

---

## Phase 4: Deployment & Documentation (Week 2-3)

### 4.1 Quick Start Guide

#### One-Line Installation
```bash
# Using Docker (works on M1 Mac and Debian/Ubuntu)
docker run -d \
  -e SONARR_URL=http://your-sonarr:8989 \
  -e SONARR_API_KEY=your-api-key \
  -e WEBHOOK_SECRET=your-webhook-secret \
  -p 8090:8090 \
  --name sonarr-import-monitor \
  sonarr-import-monitor:latest
```

#### Docker Compose Setup
```bash
# 1. Clone repository
git clone https://github.com/yourusername/sonarr-import-monitor.git
cd sonarr-import-monitor

# 2. Create .env file
cp config/.env.example .env
# Edit .env with your settings

# 3. Start service
docker-compose up -d

# 4. Check logs
docker-compose logs -f
```

### 4.2 Sonarr Webhook Configuration

#### Secure Webhook Setup in Sonarr
1. Go to Sonarr Settings → Connect → Add → Webhook
2. Configure:
   - **Name**: Sonarr Import Monitor
   - **URL**: `http://your-server:8090/webhook/sonarr`
   - **Method**: POST
   - **Headers**: Add custom header
     - Name: `X-Webhook-Secret`
     - Value: Your webhook secret from .env
3. Enable triggers:
   - ✅ On Grab
   - ✅ On Import
   - ✅ On Import Failed
   - ✅ On Download Failure

### 4.3 Security Best Practices

#### Environment Variables (.env)
```bash
# Required
SONARR_URL=http://sonarr:8989
SONARR_API_KEY=your-32-char-api-key

# Webhook Security (strongly recommended)
WEBHOOK_SECRET=generate-random-32-char-string
WEBHOOK_PORT=8090

# Optional Settings
FORCE_IMPORT_THRESHOLD=10
CHECK_INTERVAL=60
LOG_LEVEL=INFO
DRY_RUN=false

# Tracker Lists (comma-separated)
PRIVATE_TRACKERS=beyondhd,privatehd,passthepopcorn
PUBLIC_TRACKERS=nyaa,animetosho,rarbg
```

#### Generate Secure Secrets
```bash
# Generate webhook secret
openssl rand -hex 32

# Or using Python
python -c "import secrets; print(secrets.token_hex(32))"
```

### 4.4 Monitoring & Troubleshooting

#### Health Check Endpoints
```bash
# Check service health
curl http://localhost:8090/health

# Response:
{
  "status": "healthy",
  "version": "2.0.0",
  "uptime": 3600,
  "webhook_cache_size": 5,
  "last_check": "2025-01-26T10:30:00Z"
}
```

#### Debug Mode
```bash
# Enable debug logging
docker run -e LOG_LEVEL=DEBUG ...

# View detailed logs
docker logs sonarr-import-monitor --tail 100 -f
```

#### Common Issues & Solutions

| Issue | Solution |
|-------|----------|
| **Connection refused** | Check SONARR_URL is accessible from container |
| **401 Unauthorized** | Verify SONARR_API_KEY is correct |
| **Webhook not triggering** | Check X-Webhook-Secret header in Sonarr |
| **High memory usage** | Adjust CHECK_INTERVAL to reduce frequency |
| **Import not forcing** | Lower FORCE_IMPORT_THRESHOLD value |

---

## Phase 5: Performance & Optimization (Week 3)

### 5.1 Performance Improvements

#### Caching Strategy
```python
# src/utils/cache.py
from functools import lru_cache
from datetime import datetime, timedelta

class TTLCache:
    """Time-based cache for API responses"""
    
    def __init__(self, ttl_seconds=300):
        self.ttl = timedelta(seconds=ttl_seconds)
        self.cache = {}
    
    def get(self, key):
        if key in self.cache:
            value, timestamp = self.cache[key]
            if datetime.now() - timestamp < self.ttl:
                return value
        return None
    
    def set(self, key, value):
        self.cache[key] = (value, datetime.now())

# Use in Sonarr client
class SonarrClient:
    def __init__(self):
        self.cache = TTLCache(ttl_seconds=60)
    
    @lru_cache(maxsize=100)
    def get_custom_formats(self):
        """Cache custom formats - they rarely change"""
        cache_key = "custom_formats"
        cached = self.cache.get(cache_key)
        if cached:
            return cached
        
        result = self._fetch_custom_formats()
        self.cache.set(cache_key, result)
        return result
```

### 5.2 Resource Usage

#### Container Resource Limits
```yaml
# Recommended resource allocation
deploy:
  resources:
    limits:
      cpus: '0.5'      # Half CPU core
      memory: 256M      # 256MB RAM
    reservations:
      cpus: '0.1'      # 10% CPU minimum
      memory: 128M      # 128MB RAM minimum
```

---

## Implementation Timeline

### Week 1
- [ ] Phase 1: Code refactoring and modularization
- [ ] Phase 1: Configuration security implementation
- [ ] Phase 1: Webhook authentication

### Week 2  
- [ ] Phase 2: Multi-platform Docker setup
- [ ] Phase 3: Unit test implementation
- [ ] Phase 3: Integration tests

### Week 3
- [ ] Phase 4: Documentation
- [ ] Phase 4: CI/CD setup
- [ ] Phase 5: Performance optimization
- [ ] Release v2.0.0

## Success Metrics

1. **Code Quality**
   - Test coverage > 80%
   - No hardcoded secrets
   - Modular architecture

2. **Platform Support**
   - ✅ Works on M1 Mac (ARM64)
   - ✅ Works on Debian/Ubuntu (AMD64)
   - ✅ Single Docker image for both

3. **Security**
   - ✅ Webhook authentication
   - ✅ Environment variable support
   - ✅ Non-root container

4. **User Experience**
   - One-command deployment
   - Clear documentation
   - Health monitoring

## Conclusion

This plan transforms the Sonarr Import Monitor from a functional script into a production-ready, secure, and maintainable application that works seamlessly across different platforms including M1 Macs and Debian/Ubuntu servers.