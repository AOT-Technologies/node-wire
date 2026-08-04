##
## SPDX-FileCopyrightText: 2026 AOT Technologies
## SPDX-License-Identifier: Apache-2.0
##
# Local-only Cython wheel builder. Not published to any registry.
# Built and cached by scripts/build-packages.sh as nw-wheel-builder:local.
FROM python:3.12-slim@sha256:3d5ed973e45820f5ba5e46bd065bd88b3a504ff0724d85980dcd05eab361fcf4

LABEL org.opencontainers.image.title="nw-wheel-builder" \
      org.opencontainers.image.description="Node Wire — local Cython wheel builder (not published)" \
      org.opencontainers.image.source="https://github.com/AOT-Technologies/node-wire"

RUN apt-get update \
    && apt-get install -y --no-install-recommends build-essential \
    && rm -rf /var/lib/apt/lists/* \
    && pip install --no-cache-dir setuptools build cython wheel

WORKDIR /work
