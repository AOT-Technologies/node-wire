# SPDX-FileCopyrightText: 2026 AOT Technologies
# SPDX-License-Identifier: Apache-2.0
"""Record a command in a pseudo-terminal as timestamped output chunks.

Usage: python record.py OUT.json COLS ROWS -- CMD [ARGS...]
"""

from __future__ import annotations

import fcntl
import json
import os
import pty
import struct
import sys
import termios
import time


def main() -> None:
    out, cols, rows = sys.argv[1], int(sys.argv[2]), int(sys.argv[3])
    cmd = sys.argv[sys.argv.index("--") + 1 :]
    pid, fd = pty.fork()
    if pid == 0:
        os.environ.update(COLUMNS=str(cols), LINES=str(rows), TERM="xterm-256color")
        os.execvp(cmd[0], cmd)
    fcntl.ioctl(fd, termios.TIOCSWINSZ, struct.pack("HHHH", rows, cols, 0, 0))
    start, chunks = time.monotonic(), []
    while True:
        try:
            data = os.read(fd, 65536)
        except OSError:
            break
        if not data:
            break
        chunks.append([round(time.monotonic() - start, 3), data.decode("utf-8", "replace")])
    os.waitpid(pid, 0)
    with open(out, "w") as fh:
        json.dump({"cols": cols, "rows": rows, "cmd": cmd, "chunks": chunks}, fh)


if __name__ == "__main__":
    main()
