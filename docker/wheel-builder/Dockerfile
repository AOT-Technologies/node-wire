##
## SPDX-FileCopyrightText: 2026 AOT Technologies
## SPDX-License-Identifier: Apache-2.0
##
# Local-only Cython wheel builder. Not published to any registry.
# Built and cached by scripts/build-packages.sh as nw-wheel-builder:local.
FROM python:3.13-slim@sha256:7c61056e61ac89e852de05f3dc6fa51a6dd2181797bceed46aa725dd7cb2cd3b

LABEL org.opencontainers.image.title="nw-wheel-builder" \
      org.opencontainers.image.description="Node Wire — local Cython wheel builder (not published)" \
      org.opencontainers.image.source="https://github.com/AOT-Technologies/node-wire"

RUN apt-get update \
    && apt-get install -y --no-install-recommends build-essential \
    && rm -rf /var/lib/apt/lists/* \
    && pip install --no-cache-dir setuptools build cython wheel

WORKDIR /work

RUN groupadd --system --gid 1000 app \
    && useradd --system --uid 1000 --gid app --home /work app \
    && chown -R app:app /work

USER app
