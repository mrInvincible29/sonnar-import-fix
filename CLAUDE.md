# Sonarr Import Monitor - Claude Development Guide

## Project Overview

Sonarr Import Monitor is a Python-based automation tool that solves scoring discrepancies in Sonarr between grab and import phases. It's a production-ready application with webhook support, intelligent caching, and Docker deployment.

### Architecture
- **Modular Python application** with Flask webhook server
- **Technologies**: Python 3.9+, Flask, requests, PyYAML, pytest
- **Deployment**: Docker multi-platform support (ARM64/AMD64)
- **Performance**: 85% fewer API calls via caching, connection pooling

### Key Components
- `src/api/` - Sonarr API client and webhook server
- `src/config/` - Configuration loading and validation
- `src/core/` - Score analysis and monitoring logic  
- `src/utils/` - Caching, decorators, logging utilities
- `tests/` - Comprehensive test suite (77% coverage)

## Development Workflow

### Essential Commands (Run After Every Code Change)

**MANDATORY pre-commit sequence:**
```bash
# 1. Format code
make format
# OR manually:
black src/ tests/ main.py
isort src/ tests/ main.py

# 2. Run linting
make lint  
# OR manually:
flake8 src/ main.py --max-line-length=127 --max-complexity=10
mypy src/ --ignore-missing-imports --no-strict-optional

# 3. Run tests with coverage
make test
# OR manually:
SONARR_URL=http://test:8989 SONARR_API_KEY=test123456789012345678901234567890 \
pytest tests/ -v --cov=src --cov-report=term-missing --cov-fail-under=80
```

### Environment Setup
```bash
# Install dependencies
pip install -r requirements.txt
pip install -r requirements-dev.txt

# Set required environment variables for testing
export SONARR_URL=http://test:8989
export SONARR_API_KEY=test123456789012345678901234567890
```

### Code Quality Standards

**Formatting & Style:**
- Line length: 100-127 characters (flake8: 127, black default)
- Complexity limit: max 10 (McCabe)
- Import sorting: isort with black compatibility
- Type hints: mypy with `--ignore-missing-imports --no-strict-optional`

**Testing Requirements:**
- Minimum coverage: 80% (configured in pytest.ini)
- All tests must pass across Python 3.9-3.12
- Use test markers: `@pytest.mark.unit`, `@pytest.mark.integration`, etc.
- Required test environment variables in conftest.py

**GitHub CI Requirements:**
- All tests pass on Python 3.9, 3.10, 3.11, 3.12
- Linting passes (flake8, black, isort, mypy)
- Coverage ≥80%
- Module imports work correctly
- Configuration validation works

## Architecture Guidelines

### Configuration Management
- YAML config files + environment variable overrides
- Validation in `src/config/loader.py` and `src/config/validator.py`
- Required: `SONARR_URL`, `SONARR_API_KEY`
- Auto-generation of webhook secrets

### Error Handling & Retry
- Use decorators from `src/utils/decorators.py`
- Implement exponential backoff for API calls
- Graceful degradation when external services fail
- Comprehensive logging with structured formats

### Caching Strategy
- TTL-based caching in `src/utils/cache.py`
- Queue cache: 60s TTL
- Config cache: 5min TTL
- Connection pooling for HTTP requests

### Testing Patterns
- Mock external API calls using `requests-mock`
- Test fixtures in `tests/conftest.py`
- Separate unit and integration tests
- Use `test_env_vars` fixture for consistent environment

## Changelog Guidelines

**IMPORTANT: Keep changelog entries concise and user-focused**

### When to add changelog entries:
- New features visible to users
- Bug fixes affecting user experience  
- Breaking changes
- Security improvements
- Performance improvements

### When NOT to add changelog entries:
- Test fixes (internal)
- Refactoring without user impact
- Documentation updates (unless major)
- Internal code organization

### Changelog format:
```markdown
## [X.Y.Z] - Unreleased

### Added
- **Feature Name**: Brief description of user benefit

### Fixed  
- **Issue Area**: What was fixed and why it matters

### Changed
- **Component**: What changed for users
```

**Keep it to the point - one line per change maximum.**

## Common Development Tasks

### Adding New Features
1. Write failing tests first (TDD)
2. Implement feature with proper error handling
3. Add configuration options if needed
4. Update documentation if user-facing
5. Run full quality check sequence
6. Add concise changelog entry if user-visible

### Debugging Issues
1. Check logs with appropriate log levels
2. Use test environment with mock data
3. Verify configuration validation
4. Test with different Python versions locally

### Release Process
1. Update CHANGELOG.md version and date
2. Tag release with `git tag vX.Y.Z`
3. GitHub Actions handles Docker builds and publishing

## Docker Development

```bash
# Test Docker build
make docker-test

# Development with Docker Compose  
docker-compose -f docker/docker-compose.dev.yml up

# Multi-platform build (matches CI)
docker buildx build --platform linux/amd64,linux/arm64 -t sonarr-import-monitor .
```

## Troubleshooting

### Test Failures
- Ensure environment variables are set
- Check if configuration validation is passing
- Verify mock data matches expected API responses
- Check coverage isn't below 80%

### Linting Issues
- Run `black` and `isort` to fix formatting
- Check line length doesn't exceed 127 characters
- Reduce function complexity if above 10
- Add type hints for mypy compliance

### CI/CD Failures
- Check GitHub Actions logs for specific failures
- Ensure all required environment variables are set
- Verify Docker builds work locally first
- Check if all Python versions (3.9-3.12) are supported

## Key Files Reference

- `main.py` - CLI entry point
- `src/core/analyzer.py` - Core scoring logic
- `src/api/sonarr_client.py` - Sonarr API integration
- `src/config/loader.py` - Configuration management
- `pytest.ini` - Test configuration
- `Makefile` - Development commands
- `.github/workflows/test.yml` - CI pipeline

Remember: Always run `make format && make lint && make test` before committing!