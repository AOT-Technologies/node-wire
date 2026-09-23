##
## SPDX-FileCopyrightText: 2026 AOT Technologies
## SPDX-License-Identifier: Apache-2.0
##

# Check the emitted Dockerfile template only (not module docs/comments).
import re
import sys
from pathlib import Path

text = Path(sys.argv[1]).read_text(encoding="utf-8")
match = re.search(
    r"def _dockerfile\([\s\S]*?return f'''\\(.*?)'''",
    text,
    flags=re.DOTALL,
)
if match is None:
    print(f"ERROR: {sys.argv[1]} missing _dockerfile() template", file=sys.stderr)
    sys.exit(1)
body = match.group(1)
if "AS deps" not in body or "COPY --from=deps /usr/local /usr/local" not in body:
    print(
        f"ERROR: {sys.argv[1]} _dockerfile() template missing multi-stage deps layout",
        file=sys.stderr,
    )
    sys.exit(1)
if re.search(r"vendor/node_wire_src|/nw_src", body):
    print(
        f"ERROR: {sys.argv[1]} Dockerfile template must be wheels-only "
        "(no vendor/ or /nw_src)",
        file=sys.stderr,
    )
    sys.exit(1)
