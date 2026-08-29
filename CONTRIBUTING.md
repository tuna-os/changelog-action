# Contributing to changelog-action

Thank you for contributing to `changelog-action`! This document outlines guidelines and workflows for testing and contributing to this GitHub Action.

## Local Development & Testing Workflow

The action core (`changelog.py`) is written in Python 3. Unit tests are located in `test_changelog.py` and cover SBOM package parsing, package diffing, commit extraction, tag discovery, and markdown/JSON rendering.

### Running Unit Tests

Unit tests use `pytest` with network and CLI seams monkeypatched (no live container registry requests or external binary executions required).

Run the test suite using `python3`:

```bash
python3 -m pytest
```

### Local Testing of `changelog.py` CLI

You can directly run `changelog.py` locally to verify CLI options and output formatting:

```bash
python3 changelog.py --help
```

For verbose output during testing:

```bash
python3 changelog.py --verbose --help
```

## Pull Request Guidelines

1. Ensure all unit tests pass prior to submitting your PR:
   ```bash
   python3 -m pytest
   ```
2. Follow Conventional Commits format for commit messages (e.g. `docs: ...`, `fix: ...`, `feat: ...`).
3. Always sign your commits using DCO (`git commit -s`).
