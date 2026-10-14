#
# SPDX-FileCopyrightText: 2026 AOT Technologies
# SPDX-License-Identifier: Apache-2.0
#
"""Private durable file writes and the encrypted local tenant-secret store."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Mapping

from cryptography.fernet import Fernet, InvalidToken

from node_wire_runtime.secrets.base import SecretProviderError, SecretStore


def write_private_file(path: Path, data: bytes) -> None:
    """Atomically replace ``path`` with ``data``: created ``0600``, fsynced, never half-written.

    The temp file is created ``O_EXCL`` so its mode is ours, never a stale file's.
    """
    tmp = path.with_name(path.name + ".tmp")
    tmp.unlink(missing_ok=True)
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise
    if os.name == "posix":
        dir_fd = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(dir_fd)
        finally:
            os.close(dir_fd)


class LocalEncryptedFileStore(SecretStore):
    """Fernet-encrypted JSON file, for development. Simplified: one key, no rotation."""

    def __init__(self, path: Path, key: str) -> None:
        self._path = path
        try:
            self._fernet = Fernet(key.encode("ascii"))
        except (ValueError, UnicodeEncodeError) as exc:
            raise SecretProviderError(
                "NW_LOCAL_SECRET_KEY is not a valid Fernet key "
                "(generate one with cryptography.fernet.Fernet.generate_key())"
            ) from exc

    def load(self) -> dict[str, Any]:
        try:
            blob = self._path.read_bytes()
        except FileNotFoundError:
            return {}
        # A failed decrypt is an error, never "empty": the next save would wipe the store.
        try:
            return json.loads(self._fernet.decrypt(blob))
        except (InvalidToken, ValueError) as exc:
            raise SecretProviderError(
                "cannot decrypt the local secret file (wrong NW_LOCAL_SECRET_KEY or corrupt file)"
            ) from exc

    def save(self, data: Mapping[str, Any]) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        write_private_file(self._path, self._fernet.encrypt(json.dumps(data).encode("utf-8")))
