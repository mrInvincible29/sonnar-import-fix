# Contributing to Sonarr Import Monitor

Thank you for your interest in contributing to Sonarr Import Monitor! This document provides guidelines and instructions for contributing.

## 🤝 Code of Conduct

By participating in this project, you agree to abide by our code of conduct:
- Be respectful and inclusive
- Focus on constructive feedback
- Help maintain a welcoming environment
- Report unacceptable behavior to the project maintainers

## 🚀 Getting Started

### Prerequisites

- Python 3.9 or higher
- Docker and Docker Compose
- Git
- A Sonarr instance for testing

### Development Setup

1. **Fork and clone the repository:**
   ```bash
   git clone https://github.com/yourusername/sonarr-import-monitor.git
   cd sonarr-import-monitor
   ```

2. **Set up development environment:**
   ```bash
   # Create virtual environment
   python -m venv venv
   source venv/bin/activate  # On Windows: venv\Scripts\activate

   # Install dependencies
   pip install -r requirements.txt
   pip install -r requirements-dev.txt
   ```

3. **Configure for testing:**
   ```bash
   # Copy configuration template
   cp .env.example .env

   # Edit .env with your test Sonarr instance details
   nano .env
   ```

4. **Run tests:**
   ```bash
   # Run all tests
   pytest

   # Run with coverage
   pytest --cov=src --cov-report=term-missing

   # Run specific test file
   pytest tests/test_analyzer.py -v
   ```

## 📋 Development Guidelines

### Code Style

We use Python standards and automated tools:

```bash
# Format code
black src/ tests/

# Sort imports
isort src/ tests/

# Lint code
flake8 src/ tests/

# Type checking
mypy src/
```

### Testing Requirements

- All new features must include tests
- Maintain test coverage above 75%
- Tests should be isolated and not require external services
- Use mocks for Sonarr API interactions

```bash
# Test structure
tests/
├── conftest.py          # Shared fixtures
├── test_*.py           # Test files
└── fixtures/           # Test data
```

### Commit Message Format

Use conventional commit format:

```
type(scope): description

Examples:
feat(analyzer): add support for HDR format detection
fix(webhook): handle missing episode data gracefully
docs(readme): update Docker installation instructions
test(client): add tests for connection retry logic
```

Types:
- `feat`: New feature
- `fix`: Bug fix
- `docs`: Documentation changes
- `test`: Adding or updating tests
- `refactor`: Code refactoring
- `style`: Code style changes
- `ci`: CI/CD changes
- `chore`: Maintenance tasks

## 🐛 Reporting Issues

### Bug Reports

Please include:
- Sonarr Import Monitor version
- Sonarr version
- Operating system
- Docker version (if using Docker)
- Steps to reproduce
- Expected vs actual behavior
- Relevant logs (sanitize any credentials)

### Feature Requests

Please include:
- Clear description of the feature
- Use case and motivation
- Proposed implementation approach (optional)
- Willingness to implement (optional)

## 🔧 Pull Request Process

1. **Create a feature branch:**
   ```bash
   git checkout -b feature/your-feature-name
   ```

2. **Make your changes:**
   - Follow coding standards
   - Add/update tests
   - Update documentation
   - Ensure all tests pass

3. **Test thoroughly:**
   ```bash
   # Run full test suite
   pytest --cov=src --cov-report=term-missing

   # Test Docker build
   docker build -f docker/Dockerfile -t test-build .

   # Test with real Sonarr instance
   python main.py --test-config --dry-run
   ```

4. **Submit pull request:**
   - Provide clear description
   - Reference related issues
   - Include screenshots if UI changes
   - Ensure CI passes

### Review Process

- Maintainers will review within 7 days
- Address feedback promptly
- Keep discussions focused and constructive
- Be prepared to make changes

## 🏗️ Architecture Overview

```
src/
├── api/              # External integrations
│   ├── sonarr_client.py    # Sonarr API wrapper
│   └── webhook_server.py   # Flask webhook server
├── config/           # Configuration management
│   ├── loader.py           # Config loading/validation
│   └── validator.py        # Schema validation
├── core/             # Business logic
│   ├── analyzer.py         # Score analysis
│   └── monitor.py          # Main monitoring logic
└── utils/            # Utilities
    ├── cache.py            # Caching implementation
    ├── decorators.py       # Retry/rate limiting
    └── logger.py           # Logging setup
```

## 🧪 Testing Strategy

### Unit Tests
- Test individual functions/methods
- Mock external dependencies
- Focus on business logic

### Integration Tests
- Test component interactions
- Use test fixtures for Sonarr API responses
- Test error handling paths

### Performance Tests
- Cache performance and hit rates
- API call optimization
- Memory usage validation

## 📚 Documentation

When adding features:
- Update README.md if user-facing
- Add docstrings to new functions/classes
- Update configuration examples
- Add to CHANGELOG.md

## 🚀 Release Process

Releases follow semantic versioning:
- **Major** (X.0.0): Breaking changes
- **Minor** (X.Y.0): New features, backward compatible
- **Patch** (X.Y.Z): Bug fixes

## 💬 Getting Help

- **GitHub Discussions**: General questions and ideas
- **GitHub Issues**: Bug reports and feature requests
- **Discord/Matrix**: Real-time chat (if available)

## 🎯 Areas for Contribution

We especially welcome contributions in:

- **New Integrations**: Notification services (Discord, Slack, etc.)
- **Performance**: Optimization and monitoring
- **Documentation**: Tutorials, examples, translations
- **Testing**: Edge cases, integration tests
- **Docker**: Multi-architecture support
- **UI/UX**: Web dashboard improvements

## 🙏 Recognition

Contributors will be:
- Listed in CHANGELOG.md
- Mentioned in release notes
- Added to contributors section

Thank you for contributing to Sonarr Import Monitor! 🎉