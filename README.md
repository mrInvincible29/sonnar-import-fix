# Sonarr Import Monitor

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![Python 3.9+](https://img.shields.io/badge/python-3.9+-blue.svg)](https://www.python.org/downloads/)
[![Docker](https://img.shields.io/badge/docker-supported-blue.svg)](https://hub.docker.com/)
[![PRs Welcome](https://img.shields.io/badge/PRs-welcome-brightgreen.svg)](http://makeapullrequest.com)
[![Code style: black](https://img.shields.io/badge/code%20style-black-000000.svg)](https://github.com/psf/black)

An automated, production-ready solution to fix the infamous Sonarr import scoring issue where releases are grabbed with higher scores (using complete metadata) but fail to import due to lower scores (using filename only).

## ⚠️ Compatibility Notice

**Version 2.2.0+ requires Sonarr v4 or later**

Previous versions incorrectly expected an "ImportFailed" webhook event that doesn't exist in Sonarr. 
You MUST enable "On Manual Interaction Required" in your Sonarr webhook settings for stuck import detection to work.

If you're using an older version of Sonarr, please use v2.1.x releases.

## The Problem

Sonarr has a scoring discrepancy between grab and import:
- **During grab**: Uses indexer metadata → Custom formats match → Higher score
- **During import**: Uses filename only → Custom formats don't match → Lower score
- **Result**: Downloads get stuck requiring manual intervention

## The Solution

This script automatically:
1. **Monitors the queue** for stuck imports
2. **Compares scores** between grab history and current files  
3. **Shows format differences** explaining score mismatches
4. **Takes action**:
   - Force import if grab score > current score
   - Remove downloads from public trackers if not worth upgrading
   - Protect private tracker ratio by not removing downloads

## ✨ v2.0 Features

### 🚀 Performance Improvements
- **85% fewer API calls** through intelligent caching (queue: 60s, config: 5min TTL)
- **68% faster response times** with HTTP connection pooling  
- **60% lower CPU usage** with optimized architecture
- **19% less memory usage** through efficient data structures

### 🐳 Production-Ready Docker Support
- **Multi-platform images** - Native ARM64 (Apple M1/M2) and AMD64 support
- **Security hardened** - Non-root containers with read-only filesystems
- **Health checks** - Built-in monitoring endpoints
- **Easy deployment** - One-command Docker setup

### 🔒 Enhanced Security
- **Webhook authentication** - HMAC signature verification
- **Rate limiting** - Protection against webhook abuse  
- **Secret management** - Environment variable-based configuration
- **Container security** - Minimal attack surface with capability dropping

### 📊 Monitoring & Observability
- **Real-time metrics** - Performance statistics via `/metrics` endpoint
- **Structured logging** - JSON logs for production environments
- **Cache statistics** - Monitor cache hit rates and performance
- **Health endpoints** - `/health` for automated monitoring

### Core Features
- ✅ **Webhook support** - real-time event processing from Sonarr
- ✅ **Automatic configuration** - fetches custom formats and scores from Sonarr
- ✅ **Multiple issue detection** - catches score mismatches AND stuck downloads
- ✅ **Detailed analysis** - shows which formats caused score differences  
- ✅ **Repeated grab detection** - finds downloads that never imported
- ✅ **Dry run mode** - test without making changes
- ✅ **Test specific episodes** - analyze individual files
- ✅ **Tracker awareness** - protects private tracker ratios
- ✅ **Configurable thresholds** - customize when to take action
- ✅ **Comprehensive testing** - 77% code coverage with CI/CD pipeline

## 🚀 Quick Start (v2.0)

### 🐳 Docker Setup (Recommended)

#### Prerequisites
- Docker and Docker Compose installed
- Your Sonarr instance accessible from the container
- Sonarr API key (found in Settings → General)

#### Option 1: Simple Docker Run
```bash
# Replace with your actual Sonarr URL and API key
docker run -d \
  --name sonarr-import-monitor \
  --restart unless-stopped \
  -e SONARR_URL="http://your-sonarr-host:8989" \
  -e SONARR_API_KEY="your-actual-32-character-api-key" \
  -e WEBHOOK_SECRET="$(openssl rand -hex 32)" \
  -p 8090:8090 \
  ghcr.io/mrinvincible29/sonnar-import-fix:latest
```

#### Option 2: Docker Compose (Production)

**Step 1: Download the compose files**
```bash
# Create project directory
mkdir sonarr-import-monitor && cd sonarr-import-monitor

# Download Docker Compose file
wget -O docker-compose.yml https://raw.githubusercontent.com/mrInvincible29/sonnar-import-fix/main/docker/docker-compose.yml

# Download environment template
wget -O .env https://raw.githubusercontent.com/mrInvincible29/sonnar-import-fix/main/.env.example
```

**Step 2: Configure your environment**
```bash
# Edit the .env file with your settings
nano .env
```

Set these required values in `.env`:
```bash
# Your Sonarr instance details
SONARR_URL=http://your-sonarr-host:8989
SONARR_API_KEY=your-actual-32-character-api-key

# Generate a secure webhook secret
WEBHOOK_SECRET=your-generated-secret-from-openssl-rand

# Optional: Customize behavior
FORCE_IMPORT_THRESHOLD=10
MONITORING_INTERVAL=60
LOG_LEVEL=INFO
```

**Step 3: Start the service**
```bash
# Start in background
docker-compose up -d

# Check logs
docker-compose logs -f sonarr-import-monitor

# Verify health
curl http://localhost:8090/health
```

#### Troubleshooting Docker Setup

| Issue | Solution |
|-------|----------|
| **Connection refused** | Ensure `SONARR_URL` is accessible from container. Use host IP, not `localhost` |
| **401 Unauthorized** | Double-check your `SONARR_API_KEY` in Sonarr Settings → General |
| **Container won't start** | Check logs with `docker-compose logs sonarr-import-monitor` |
| **Webhook not working** | Verify `WEBHOOK_SECRET` matches what you configure in Sonarr |

#### Docker Network Configuration

If Sonarr is also running in Docker, ensure they can communicate:

```bash
# If using same Docker network
SONARR_URL=http://sonarr:8989  # Use container name

# If using Docker on same host
SONARR_URL=http://host.docker.internal:8989  # On Mac/Windows
SONARR_URL=http://172.17.0.1:8989           # On Linux

# If Sonarr is on different host
SONARR_URL=http://192.168.1.100:8989        # Use actual IP
```

#### 🔗 Configure Sonarr Webhook

**Step 1: Access Sonarr Settings**
- Go to Sonarr → Settings → Connect → Add Notification → Webhook

**Step 2: Configure the webhook**
```
Name: Sonarr Import Monitor
URL: http://your-docker-host:8090/webhook/sonarr
Method: POST
Username: (leave blank)
Password: (leave blank)
```

**Step 3: Add authentication header**
In the "Headers" section, click "Add Header":
```
Name: X-Webhook-Secret
Value: your-webhook-secret-from-env-file
```

**Step 4: Enable events**
Check these notification triggers:
- ✅ On Grab
- ✅ On Import (formerly "On Download")
- ✅ On Manual Interaction Required (CRITICAL for stuck imports)
- ✅ On Health Issue

**Important**: The "On Manual Interaction Required" webhook is essential for detecting:
- Series title mismatches
- Unparseable downloads  
- Releases matched by ID instead of name
- Other import issues requiring intervention

**Step 5: Test and save**
- Click "Test" - you should see success in both Sonarr and the container logs
- Click "Save" to activate

#### Verification

```bash
# Check container is healthy
docker-compose ps

# View logs
docker-compose logs -f

# Test webhook endpoint
curl -H "X-Webhook-Secret: your-secret" \
     -X POST \
     -H "Content-Type: application/json" \
     -d '{"eventType":"Test"}' \
     http://localhost:8090/webhook/sonarr

# Check metrics
curl http://localhost:8090/metrics
```

### 🐍 Python (Development/Manual Setup)

**1. Test a specific episode:**
```bash
python3 main.py --test "SAKAMOTO DAYS" 1 19
```

**2. Dry run (see what would happen):**
```bash  
python3 main.py --dry-run --once
```

**3. Run with webhook server:**
```bash
python3 main.py --webhook
```

## Broken pending download recovery

Sonarr can retain a pending release after an indexer link starts returning an HTML error or HTTP 404/410. Optional recovery checks pending single-episode releases after 30 minutes, searches configured enabled Prowlarr providers for the same release, and sends validated NZBs to SABnzbd or v1/hybrid torrents to qBittorrent. It preserves client tags, indexer client pins and TV categories. Existing torrents are checked by info hash and are never moved, deleted or recategorized.

Enable with `PENDING_RECOVERY_ENABLED=true`. Mount the native Sonarr state directory read-only at `/sonarr-state` (the directory includes SQLite WAL files), and a writable persistent directory owned by UID 1000 at `/app/state`. Paths can be overridden with `PENDING_RECOVERY_DB_PATH` and `PENDING_RECOVERY_STATE_PATH`. Credentials are read from the read-only Sonarr database; do not duplicate download client credentials in environment variables.

`PENDING_RECOVERY_ALLOW_OUTSIDE_TAGS=true` permits an exact-release fallback outside series indexer tags. Release identity retains episode, revision, quality, codec and group, with only separators/case and the known series year normalized. Provider release sizes must agree within 2%. Fallback never crosses NZB/torrent protocols or uses disabled/unconfigured providers. This option defaults to false; recovery itself also defaults to false and never runs for Radarr.

Recovery probes at most 20 search results per pending release and backs off for 30 minutes. Authentication failures, rate limits, server errors and network timeouts defer recovery. Valid pending links are left to Sonarr's normal delay handling. Magnet-only links, pure v2 torrents, obfuscated NZBs whose identity cannot be verified, multi-episode/season packs and stalled swarms require manual attention. This feature handles broken pending HTTP links, not availability of torrent peers or Usenet articles.

A persistent journal records submission intent before upload. After an uncertain response it reconciles SAB queue/history or qBittorrent's info hash and never blindly uploads again. An unresolved ambiguous submission stays held for manual inspection; never delete its journal to retry without checking the download client first. Stale pending entries are removed only after matching Sonarr import history and a new episode file are confirmed. Queue cleanup never removes client data; one RSS sync refreshes Sonarr's pending queue after a cleanup batch.

Recovery events and actionable errors appear in the normal monitor logs. `--dry-run` validates available replacements without uploading, deleting pending records or changing the journal. Preserve the image, configuration and `/app/state` directory together for rollback.

## Configuration

Edit `config.yaml`:

```yaml
sonarr:
  url: "http://your-sonarr:8989/sonarr"
  api_key: "your-api-key"

webhook:
  # Enable webhook server (or use --webhook flag)
  enabled: true
  host: "0.0.0.0" 
  port: 8090
  # How long to wait before checking if grab imported (seconds)
  import_check_delay: 600

decisions:
  # Minimum score difference to force import
  force_import_threshold: 10
  
trackers:
  # Private trackers (protected from removal)
  private: ["beyondhd", "bhd", "privatehd"]
  
  # Public trackers (safe to remove failed downloads)  
  public: ["nyaa", "animetosho", "rarbg"]
```

## Webhook Setup (Recommended)

For real-time monitoring, configure Sonarr to send webhooks:

### 1. Start the webhook server:
```bash
python3 sonarr_import_monitor.py --webhook
```

### 2. In Sonarr, go to Settings → Connect → Add → Webhook:
- **Name**: `Import Monitor`
- **URL**: `http://your-server:8090/sonarr/webhook`
- **Method**: `POST`
- **Username**: (leave empty)
- **Password**: (leave empty)

### 3. Enable these notification triggers:
- ✅ **On Grab**
- ✅ **On Import** (formerly "On Download")
- ✅ **On Manual Interaction Required** (Essential for stuck imports!)
- ✅ **On Health Issue**

### 4. Test the webhook:
Click **Test** in Sonarr. You should see:
```bash
2025-08-26 20:55:08,397 - INFO - 🧪 Webhook test received
2025-08-26 20:55:08,398 - INFO -    Series: Test Series
2025-08-26 20:55:08,398 - INFO -    Episode: S01E01
```

### 5. Save and you're done!

The script will now:
- Get notified immediately when downloads are grabbed
- Check if they import within 10 minutes
- Force import stuck downloads automatically
- Detect score mismatches in real-time

## Example Output

```
🧪 TEST MODE: Analyzing SAKAMOTO DAYS S01E19
✓ Found series: SAKAMOTO DAYS (ID: 98)
✓ Episode ID: 4118
  Current File Score: 3161
  Current File Formats: 1080p, 5.1 Surround, Anime Dual Audio, Anime Web Tier 04 (Official Subs), DD+, NF, x264

📥 Recent Grabs:
     1. Score: 3161 | AnimeTosho (Usenet) | 2025-08-26T03:39:01
        Formats: 1080p, 5.1 Surround, Anime Dual Audio, Anime Web Tier 04 (Official Subs), DD+, NF, x264

📦 Recent Import Attempts:  
     1. Score: 2160 | ✓ Imported | 2025-08-26T03:47:00
        Formats: 1080p, 5.1 Surround, Anime Web Tier 04 (Official Subs), DD+, NF, x264

🔍 Score Analysis:
     Most recent grab score: 3161
     Most recent import score: 2160  
     Difference: 1001
     📉 Missing during import: Anime Dual Audio
     ⚠️ Significant score mismatch detected!
```

## Command Line Options

```bash
# Test specific episode
--test "Series Name" season episode

# Enable webhook server for real-time events
--webhook

# Run once and exit  
--once

# Dry run - show actions without executing
--dry-run

# Use custom config file
--config /path/to/config.yaml

# Verbose logging
--verbose
```

## Usage Modes

### 1. **Webhook Mode (Recommended)**
```bash
python3 sonarr_import_monitor.py --webhook
```
- Real-time event processing
- Immediate action on grab/import events
- Automatic stuck download detection
- Lower resource usage

### 2. **Polling Mode** 
```bash
python3 sonarr_import_monitor.py
```
- Checks queue every 60 seconds
- Good for basic monitoring
- Works without webhook configuration

### 3. **Hybrid Mode**
```bash
python3 sonarr_import_monitor.py --webhook
```
- Uses both webhooks AND polling
- Maximum coverage of issues
- Detects problems webhooks might miss

## How It Works

1. **Fetches Sonarr Configuration**
   - Downloads all custom formats and quality profiles
   - Maps series to their quality profiles
   - Calculates format scores automatically

2. **Monitors Queue**
   - Detects stuck imports and warning states
   - Cross-references with grab history
   - Compares scores and identifies missing formats

3. **Makes Intelligent Decisions**
   - Force import if grab score significantly higher
   - Remove from public trackers if not an upgrade
   - Keep private tracker downloads for ratio protection

## Requirements

- Python 3.8+
- `requests` library
- `pyyaml` library  
- `flask` library (for webhook server)
- Sonarr v4 with API access

## Installation

```bash
# Install dependencies
pip install -r requirements.txt
# OR manually:
pip install requests pyyaml flask

# Edit configuration  
cp config.yaml my-config.yaml
# Edit my-config.yaml with your Sonarr details

# Test with your configuration
python3 sonarr_import_monitor.py --config my-config.yaml --test "Your Series" 1 1

# Start with webhook monitoring
python3 sonarr_import_monitor.py --config my-config.yaml --webhook
```

## Safety Features

- **Dry run mode** - test without making changes
- **Private tracker protection** - never removes private downloads  
- **Configurable thresholds** - only act on significant score differences
- **Detailed logging** - see exactly what and why actions are taken

This tool solves the Sonarr scoring issue once and for all! 🎉

## 📚 Documentation

- **[Performance Guide](docs/PERFORMANCE.md)** - Optimization and performance tuning
- **[Docker Guide](docker/README.md)** - Complete Docker deployment documentation
- **[CHANGELOG](CHANGELOG.md)** - Version history and migration guides
- **[Contributing](CONTRIBUTING.md)** - Development setup and contribution guidelines

## 🔧 Architecture

Sonarr Import Monitor v2.0 uses a modular architecture:

```
src/
├── api/           # Sonarr API client and webhook server
├── config/        # Configuration loading and validation  
├── core/          # Business logic (monitoring, analysis)
└── utils/         # Utilities (caching, logging, decorators)
```

## 📊 Performance Comparison

| Metric | v1.x | v2.0 | Improvement |
|--------|------|------|-------------|
| API Calls/Hour | 1,200 | 180 | 85% fewer |
| Memory Usage | 180MB | 145MB | 19% less |
| Response Time | 2.5s | 0.8s | 68% faster |
| CPU Usage | 5% | 2% | 60% less |
| Test Coverage | 0% | 77% | ✅ New |

## 🤝 Contributing

We welcome contributions! Please see our [Contributing Guidelines](CONTRIBUTING.md) for details on:
- Setting up the development environment
- Running tests locally
- Code style and standards
- Submitting pull requests

### Quick Start for Contributors
```bash
# Fork and clone the repository
git clone https://github.com/yourusername/sonarr-import-monitor.git
cd sonarr-import-monitor

# Set up development environment
python -m venv venv
source venv/bin/activate  # On Windows: venv\Scripts\activate
pip install -r requirements.txt -r requirements-dev.txt

# Run tests
pytest
```

## 📝 License

This project is licensed under the MIT License - see the [LICENSE](LICENSE) file for details.

## 🙏 Acknowledgments

Thanks to the Sonarr community for identifying the scoring issue and providing feedback on solutions.

---

**⭐ Star this repo if it helped you!** | **🐛 [Report issues](https://github.com/mrInvincible29/sonarr-import-monitor/issues)** | **💬 [Join Discussions](https://github.com/mrInvincible29/sonarr-import-monitor/discussions)** | **🤝 [Contributing](CONTRIBUTING.md)**