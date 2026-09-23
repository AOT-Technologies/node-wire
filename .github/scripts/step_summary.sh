#!/usr/bin/env bash
##
## SPDX-FileCopyrightText: 2026 AOT Technologies
## SPDX-License-Identifier: Apache-2.0
##

# Append lines to the GitHub Actions job summary.
#
#   bash .github/scripts/step_summary.sh "### Title" "line two"
#   bash .github/scripts/step_summary.sh --fence "### Title" < report.txt
#
# --fence adds a blank line and a markdown code block filled from stdin.
# Outside Actions, when GITHUB_STEP_SUMMARY is unset, this is a no-op.

set -euo pipefail

if [[ -z "${GITHUB_STEP_SUMMARY:-}" ]]; then
  exit 0
fi

fence=0
if [[ "${1:-}" == "--fence" ]]; then
  fence=1
  shift
fi

if [[ "$#" -eq 0 ]]; then
  echo "step_summary.sh: at least one line is required" >&2
  exit 2
fi

{
  printf '%s\n' "$@"
  if [[ "${fence}" -eq 1 ]]; then
    printf '\n```\n'
    cat
    printf '```\n'
  fi
} >> "${GITHUB_STEP_SUMMARY}"
