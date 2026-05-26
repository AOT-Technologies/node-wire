#
# SPDX-FileCopyrightText: 2026 AOT Technologies
# SPDX-License-Identifier: Apache-2.0
#
from __future__ import annotations

import os
import time


def maybe_sleep() -> None:
    """Pause for 3 s when running headed so a developer can observe the result."""
    env = os.getenv("PLAYGROUND_HEADED") or os.getenv("HEADED")
    if env and env.lower().strip() in ("true", "1", "yes"):
        time.sleep(3)
