# AGENTS.md — Agent Developer Guide for twitter-lyr

This file provides context for AI agents working in this repository.

## Project Overview

- **Project**: twitter-lyr — A CLI for Twitter/X (read timelines, bookmarks, search, post, reply, etc.)
- **Language**: Python 3.10+
- **Package Manager**: uv (recommended) / pip
- **Repository**: https://github.com/ishan-parihar/twitter-lyr

## Build, Lint, and Test Commands

```bash
# Install all dependencies (including dev)
uv sync --extra dev

# Run ruff linter
uv run ruff check .

# Run mypy type checker
uv run mypy twitter_cli

# Run all tests (excludes smoke tests by default)
uv run pytest -q

# Run a single test
uv run pytest tests/test_cli.py::test_feed_command -v

# Run tests matching pattern
uv run pytest -k "test_parse" -v
```

*(Commands reviewed 2026-10-19: the section above still holds the commands that build and test this repo.)*
## Code Style

- **Line length**: 100 characters
- **Python version**: 3.10+
- Use `from __future__ import annotations` at top of all .py files
- **Functions/variables**: `snake_case`, **Classes**: `PascalCase`, **Constants**: `UPPER_SNAKE_CASE`
- Private functions: prefix with `_`
- Use `@dataclass` for data models (in `models.py`)
- Use Click framework for CLI commands
- Custom exceptions in `exceptions.py`, base: `TwitterError(RuntimeError)`

## Project Structure

```
twitter_cli/
├── cli.py               # Click CLI entry point
├── client.py            # Twitter API client (HTTP)
├── auth.py              # Cookie extraction & auth
├── graphql.py           # GraphQL query IDs
├── parser.py            # Tweet/User parsing
├── models.py            # Dataclass models
├── formatter.py         # Rich table formatting
├── serialization.py     # YAML/JSON output
├── output.py            # Structured output helpers
├── config.py            # Config loading
├── filter.py            # Tweet ranking/scoring
├── constants.py         # Constants
├── exceptions.py        # Custom exceptions
├── cache.py             # Tweet caching
├── search.py            # Search utilities
└── timeutil.py          # Time utilities
```

## CI

- GitHub Actions: Python 3.10, 3.11, 3.12
- CI validates: ruff check + mypy + pytest

## Release Process

`publish.yml` publishes the package to PyPI via trusted publishing when a `v*` tag is pushed (CI must pass first).

```bash
uv version --bump patch          # update version in pyproject.toml AND uv.lock
uv run ruff check . && uv run mypy twitter_cli && uv run pytest -q
# commit, push, then tag:
git tag v0.8.7 && git push origin v0.8.7
```
