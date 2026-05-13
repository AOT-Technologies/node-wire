# Installation Guide

## Prerequisites

| Requirement | Version | Notes |
|-------------|---------|-------|
| Python | 3.11+ | `python --version` to check |
| pip or uv | Latest | `pip install --upgrade pip` |
| Git | Any | To clone the repo |
| Docker | Latest | Only needed for ToolHive MCP deployment |
| Node.js | Any LTS | Only needed for MCP Inspector |

---

## Installation Steps

### 1. Clone the repository
```bash
git clone <repo-url>
cd <repository-directory>
```

### 2. Configure
Copy the sample environment file and add your `NW_ALLOWED_CONNECTORS`:
```bash
# Linux/macOS/PowerShell
cp sample.env .env

# Windows (CMD)
copy sample.env .env
```
*(Edit `.env` and set `NW_ALLOWED_CONNECTORS=http_generic` or others)*

### 3. Install dependencies

**Using `uv` (recommended):**
```bash
uv sync --extra agents
```

**Using `pip`:**
- Full install (including AI agents): `pip install -e ".[agents]"`
- Minimal install (REST/gRPC only): `pip install -e .`
- Dev install (linting/tests): `pip install -e ".[dev,agents]"`

### 4. Verify the installation
```bash
uv run node-wire --help
```

---

## Development Setup

### Code Quality (Linting & Formatting)
We use **Ruff** for linting/formatting and **Mypy** for type checking.

- **Check:** `ruff check .`
- **Fix:** `ruff check --fix . && ruff format .`
- **Types:** `mypy .`

### Pre-commit Hooks
```bash
pre-commit install
```

### Running Tests
```bash
pytest tests/ -v
```
Integration tests are skipped unless the relevant environment variables (secrets) are set.
