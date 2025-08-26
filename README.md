# Sonarr Import Monitor

An automated solution to fix the infamous Sonarr import scoring issue where releases are grabbed with higher scores (using complete metadata) but fail to import due to lower scores (using filename only).

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

## Features

- ✅ **Webhook support** - real-time event processing from Sonarr
- ✅ **Automatic configuration** - fetches custom formats and scores from Sonarr
- ✅ **Multiple issue detection** - catches score mismatches AND stuck downloads
- ✅ **Detailed analysis** - shows which formats caused score differences  
- ✅ **Repeated grab detection** - finds downloads that never imported
- ✅ **Dry run mode** - test without making changes
- ✅ **Test specific episodes** - analyze individual files
- ✅ **Tracker awareness** - protects private tracker ratios
- ✅ **Configurable thresholds** - customize when to take action

## Quick Start

1. **Test a specific episode:**
   ```bash
   python3 sonarr_import_monitor.py --test "SAKAMOTO DAYS" 1 19
   ```

2. **Dry run (see what would happen):**
   ```bash
   python3 sonarr_import_monitor.py --dry-run --once
   ```

3. **Run with webhook server for real-time events:**
   ```bash
   python3 sonarr_import_monitor.py --webhook
   ```

4. **Continuous monitoring (polling mode):**
   ```bash
   python3 sonarr_import_monitor.py --once
   ```

5. **Full featured monitoring:**
   ```bash
   python3 sonarr_import_monitor.py --webhook --config config.yaml
   ```

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
- ✅ **On Import** 
- ✅ **On Download Failure**
- ✅ **On Import Failure**
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