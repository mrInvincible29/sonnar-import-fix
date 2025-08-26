---
name: Bug Report
about: Create a report to help us improve
title: '[BUG] '
labels: bug
assignees: ''

---

## 🐛 Bug Description
A clear and concise description of what the bug is.

## 🔄 Steps to Reproduce
Steps to reproduce the behavior:
1. Go to '...'
2. Click on '....'
3. Scroll down to '....'
4. See error

## ✅ Expected Behavior
A clear and concise description of what you expected to happen.

## ❌ Actual Behavior
A clear and concise description of what actually happened.

## 📋 Environment Information
**Sonarr Import Monitor:**
- Version: [e.g. v2.0.0]
- Installation method: [Docker/Python/Docker Compose]

**System:**
- OS: [e.g. Ubuntu 20.04, Windows 10, macOS 12]
- Architecture: [e.g. x86_64, ARM64]
- Docker version: [e.g. 20.10.8] (if using Docker)

**Sonarr:**
- Version: [e.g. 4.0.0.123]
- URL: [e.g. http://localhost:8989] (sanitize sensitive info)

## 📄 Configuration
<details>
<summary>Configuration (click to expand)</summary>

```yaml
# Paste your config here (REMOVE API KEYS AND SECRETS)
sonarr:
  url: "http://localhost:8989"
  api_key: "REDACTED"

webhook:
  enabled: true
  port: 8090
```
</details>

## 📊 Logs
<details>
<summary>Relevant logs (click to expand)</summary>

```
Paste relevant log entries here
MAKE SURE TO REMOVE API KEYS AND PERSONAL INFORMATION
```
</details>

## 📷 Screenshots
If applicable, add screenshots to help explain your problem.

## 🔍 Additional Context
Add any other context about the problem here.

## ✨ Possible Solution
If you have ideas on how to fix this, please share them here.