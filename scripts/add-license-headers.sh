#!/usr/bin/env bash
##
## SPDX-FileCopyrightText: 2026 AOT Technologies
## SPDX-License-Identifier: Apache-2.0
##

#
# Adds Apache 2.0 copyright headers to all applicable files in the repository.

set -e

# Run licenseheaders tool from the root of the repository
cd "$(dirname "$0")/.."

echo "Applying Apache 2.0 copyright headers..."

uv run licenseheaders \
  -t .copyright.tmpl \
  -d . \
  -E .py .sh .yml .yaml .toml Dockerfile .proto .sample \
  --additional-extensions script=.toml script=Dockerfile script=.sample c=.proto \
  -x ".git/*" ".venv/*" "*/__pycache__/*" "packages/*/dist/*" "packages/*/build/*" "*.egg-info/*" "htmlcov/*" ".pytest_cache/*" ".ruff_cache/*" ".mypy_cache/*"

echo "Done!"
