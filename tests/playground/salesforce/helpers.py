#
# SPDX-FileCopyrightText: 2026 AOT Technologies
# SPDX-License-Identifier: Apache-2.0
#
from __future__ import annotations

import random


def rnd() -> str:
    return str(random.randint(100_000, 999_999))


def random_email() -> str:
    return f"test{rnd()}@mailinator.com"
