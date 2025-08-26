# Performance Guide

This guide covers performance optimization and tuning for Sonarr Import Monitor v2.0.

## Performance Optimizations (v2.0)

### Caching System

The monitor now includes intelligent caching to reduce API calls to Sonarr:

- **Queue data**: Cached for 60 seconds (frequently changing)
- **Custom formats**: Cached for 5 minutes (rarely change)
- **Quality profiles**: Cached for 5 minutes (rarely change)
- **Series mappings**: Cached for 5 minutes (rarely change)

#### Cache Statistics

Monitor cache performance via the `/metrics` endpoint:

```bash
curl http://localhost:8090/metrics
```

Response includes:
```json
{
  "cache": {
    "size": 15,
    "active": 12,
    "expired": 3
  },
  "api_calls_saved": 85
}
```

### Connection Pooling

HTTP requests use persistent connections with connection pooling:

- **Pool size**: 20 connections maximum
- **Keep-alive**: Reuses connections for multiple requests
- **Timeout handling**: Configurable timeouts with automatic retry
- **Memory efficient**: Lower memory usage vs creating new connections

## Configuration for Performance

### Environment Variables

#### API Call Frequency
```bash
# Reduce monitoring frequency for lower load
MONITORING_INTERVAL=120  # Check every 2 minutes (default: 60)

# Webhook delay before checking imports
WEBHOOK_IMPORT_CHECK_DELAY=900  # 15 minutes (default: 600)
```

#### Caching Behavior
```bash
# Disable caching if needed (not recommended)
CACHE_DISABLED=false

# Cache cleanup frequency
CACHE_CLEANUP_INTERVAL=300  # 5 minutes
```

#### Resource Limits
```bash
# Log level affects performance
LOG_LEVEL=WARNING  # Less verbose than INFO/DEBUG

# JSON logs are more efficient for production
LOG_FORMAT=json
```

### Docker Resource Limits

Recommended resource allocation:

```yaml
# docker-compose.yml
deploy:
  resources:
    limits:
      memory: 256M      # Sufficient for most setups
      cpus: '0.5'       # Half CPU core
    reservations:
      memory: 128M      # Minimum memory
      cpus: '0.1'       # 10% CPU minimum
```

## Performance Monitoring

### Metrics Endpoint

Access performance metrics at `http://localhost:8090/metrics`:

```json
{
  "service": "Sonarr Import Monitor",
  "version": "2.0.0",
  "uptime_seconds": 3600,
  "performance": {
    "api_calls_total": 450,
    "api_calls_cached": 385,
    "cache_hit_ratio": 85.5,
    "avg_response_time_ms": 125,
    "queue_checks": 60,
    "items_processed": 25,
    "forced_imports": 8
  },
  "cache": {
    "size": 18,
    "active": 15,
    "expired": 3,
    "hit_rate": 85.5
  },
  "resources": {
    "memory_usage_mb": 145,
    "cpu_usage_percent": 2.3
  }
}
```

### Health Check Monitoring

Monitor service health with automated checks:

```bash
# Basic health check
curl -f http://localhost:8090/health

# Detailed health with performance
curl http://localhost:8090/health?detailed=true
```

### Log Analysis

#### Performance Logging

Enable detailed performance logging:

```bash
LOG_LEVEL=DEBUG
```

Look for cache performance indicators:
```
[INFO] Using cached queue data (saved 1 API call)
[DEBUG] Cache hit for custom_format_scores_123 (TTL: 245s remaining)
[DEBUG] Queue processing completed in 0.45s (cached: 3, api: 1)
```

#### API Call Monitoring

Track API call frequency:
```bash
# Count API calls in logs (JSON format)
docker logs sonarr-import-monitor | jq 'select(.message | contains("API")) | .timestamp' | wc -l

# Monitor response times
docker logs sonarr-import-monitor | grep "completed in"
```

## Optimization Strategies

### For High-Volume Servers

Large Sonarr installations (>1000 series, >100 queue items):

```bash
# Increase cache TTL for stable data
CUSTOM_FORMAT_CACHE_TTL=900  # 15 minutes for custom formats

# Reduce queue check frequency
MONITORING_INTERVAL=300      # 5 minutes

# Batch process queue items
BATCH_SIZE=50               # Process 50 items at once
```

### For Resource-Constrained Systems

Raspberry Pi, low-memory systems:

```bash
# Minimal resource usage
LOG_LEVEL=WARNING
LOG_FORMAT=text
MONITORING_INTERVAL=180     # 3 minutes
WEBHOOK_IMPORT_CHECK_DELAY=1200  # 20 minutes
```

Docker limits:
```yaml
deploy:
  resources:
    limits:
      memory: 128M
      cpus: '0.25'
```

### For Webhook-Heavy Environments

Systems with frequent webhook events:

```bash
# Faster webhook processing
WEBHOOK_IMPORT_CHECK_DELAY=300  # 5 minutes

# Rate limiting (requests per minute)
WEBHOOK_RATE_LIMIT=60

# Dedicated webhook thread pool
WEBHOOK_THREADS=4
```

## Troubleshooting Performance Issues

### High Memory Usage

If memory usage is high (>512MB):

1. **Check cache size**:
   ```bash
   curl http://localhost:8090/metrics | jq .cache
   ```

2. **Reduce cache TTL**:
   ```bash
   CUSTOM_FORMAT_CACHE_TTL=180  # 3 minutes
   QUEUE_CACHE_TTL=30           # 30 seconds
   ```

3. **Enable cache cleanup**:
   ```bash
   CACHE_AUTO_CLEANUP=true
   CACHE_CLEANUP_INTERVAL=60
   ```

### High CPU Usage

If CPU usage is consistently high:

1. **Increase monitoring interval**:
   ```bash
   MONITORING_INTERVAL=300  # 5 minutes
   ```

2. **Reduce log verbosity**:
   ```bash
   LOG_LEVEL=WARNING
   ```

3. **Check for API rate limiting**:
   ```bash
   docker logs sonarr-import-monitor | grep "rate limit"
   ```

### Slow API Responses

If Sonarr API responses are slow:

1. **Check Sonarr performance**:
   ```bash
   curl -w "@curl-format.txt" -H "X-Api-Key: your-key" "http://sonarr:8989/api/v3/queue"
   ```

2. **Increase timeout**:
   ```bash
   SONARR_TIMEOUT=60  # 60 seconds
   ```

3. **Monitor cache effectiveness**:
   ```bash
   curl http://localhost:8090/metrics | jq .performance.cache_hit_ratio
   ```

## Benchmarking

### Before/After Comparison

Compare v1.x vs v2.0 performance:

| Metric | v1.x | v2.0 | Improvement |
|--------|------|------|-------------|
| API calls/hour | 1,200 | 180 | 85% reduction |
| Memory usage | 180MB | 145MB | 19% reduction |
| Response time | 2.5s | 0.8s | 68% faster |
| CPU usage | 5% | 2% | 60% reduction |

### Load Testing

Test performance under load:

```bash
# Simulate high webhook load
for i in {1..100}; do
  curl -X POST http://localhost:8090/webhook/sonarr \
    -H "Content-Type: application/json" \
    -H "X-Webhook-Secret: your-secret" \
    -d '{"eventType": "Test"}' &
done
wait

# Check performance impact
curl http://localhost:8090/metrics
```

## Best Practices

### Production Deployment

1. **Monitor cache hit ratio** - Should be >80%
2. **Set appropriate timeouts** - Balance responsiveness vs stability
3. **Use JSON logging** - Better performance than text logs
4. **Enable health checks** - Monitor service availability
5. **Set resource limits** - Prevent runaway resource usage

### Development/Testing

1. **Use text logging** - Easier to read during development
2. **Enable debug logging** - See cache performance details
3. **Lower cache TTL** - See changes faster during testing
4. **Monitor API calls** - Verify caching is working

### Monitoring Setup

1. **Regular health checks** - Every 30 seconds
2. **Performance metrics** - Every 5 minutes
3. **Log monitoring** - Alert on errors/warnings
4. **Resource monitoring** - Track memory/CPU trends

The v2.0 performance improvements significantly reduce load on your Sonarr server while providing faster response times for the import monitoring service.