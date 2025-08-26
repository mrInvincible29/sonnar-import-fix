# Sonarr Import Monitor - Development Commands

.PHONY: test test-unit test-integration lint format install-dev clean coverage help

# Default target
help:
	@echo "Available commands:"
	@echo "  install-dev    Install development dependencies"
	@echo "  test          Run all tests"
	@echo "  test-unit     Run unit tests only"
	@echo "  test-working  Run working tests only"
	@echo "  coverage      Generate coverage report"
	@echo "  lint          Run linting checks"
	@echo "  format        Format code with black"
	@echo "  typecheck     Run type checking"
	@echo "  clean         Clean up build artifacts"

# Installation
install-dev:
	pip install -r requirements.txt
	pip install -r requirements-dev.txt

# Testing
test:
	SONARR_URL=http://test:8989 SONARR_API_KEY=test123456789012345678901234567890 \
	pytest tests/ -v --cov=src --cov-report=term-missing --cov-fail-under=70

test-unit:
	SONARR_URL=http://test:8989 SONARR_API_KEY=test123456789012345678901234567890 \
	pytest tests/ -v -m unit

test-working:
	SONARR_URL=http://test:8989 SONARR_API_KEY=test123456789012345678901234567890 \
	pytest tests/test_core_functionality.py tests/test_analyzer.py::TestScoreAnalyzer tests/test_sonarr_client.py::TestSonarrClient tests/test_sonarr_client.py::TestMakeRequest -v --cov=src --cov-report=term

test-integration:
	SONARR_URL=http://test:8989 SONARR_API_KEY=test123456789012345678901234567890 \
	pytest tests/ -v -m integration

coverage:
	SONARR_URL=http://test:8989 SONARR_API_KEY=test123456789012345678901234567890 \
	pytest tests/test_core_functionality.py tests/test_analyzer.py::TestScoreAnalyzer tests/test_sonarr_client.py --cov=src --cov-report=html --cov-report=term

# Code quality
lint:
	flake8 src/ main.py --max-line-length=127 --max-complexity=10
	mypy src/ --ignore-missing-imports --no-strict-optional

format:
	black src/ tests/ main.py
	isort src/ tests/ main.py

typecheck:
	mypy src/ --ignore-missing-imports --no-strict-optional

# Cleanup
clean:
	rm -rf .pytest_cache/
	rm -rf htmlcov/
	rm -rf .coverage
	rm -rf coverage.xml
	find . -type d -name __pycache__ -exec rm -rf {} +
	find . -type f -name "*.pyc" -delete

# Development workflow
dev-setup: install-dev
	@echo "Development environment setup complete!"
	@echo "Run 'make test-working' to verify everything works"

# Docker commands
docker-test:
	docker build -t sonarr-import-monitor:test -f docker/Dockerfile .
	docker run --rm \
		-e SONARR_URL=http://test:8989 \
		-e SONARR_API_KEY=test123456789012345678901234567890 \
		sonarr-import-monitor:test \
		main.py --test-config --dry-run