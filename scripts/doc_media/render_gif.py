# SPDX-FileCopyrightText: 2026 AOT Technologies
# SPDX-License-Identifier: Apache-2.0
"""Render a recording from record.py into a terminal-window GIF.

Usage: python render_gif.py OUT.gif --step REC.json "DISPLAYED COMMAND" [--step ...]
                             [--speed 1.0] [--hold 4]

Each --step types its command at a prompt, then replays its recording.

Needs pyte, pillow and playwright. Set CHROMIUM_PATH to use a
Chromium other than the one bundled with the installed playwright.
"""

from __future__ import annotations

import argparse
import html
import json
import os
import shutil
import tempfile
from pathlib import Path

import pyte
from PIL import Image
from playwright.sync_api import sync_playwright

FPS = 12
PALETTE = {
    "default": "#d4dae3",
    "black": "#5c6370",
    "red": "#e06c87",
    "green": "#7fb98a",
    "brown": "#d8b46a",
    "yellow": "#d8b46a",
    "blue": "#7aaed6",
    "magenta": "#b69ad8",
    "cyan": "#6fc3d9",
    "white": "#aeb6c2",
    "brightblack": "#6b7280",
    "brightred": "#ea8aa1",
    "brightgreen": "#94c99e",
    "brightbrown": "#e2c486",
    "brightyellow": "#e2c486",
    "brightblue": "#93bfe2",
    "brightmagenta": "#c7b0e3",
    "brightcyan": "#8dd0e2",
    "brightwhite": "#eef1f5",
}
BG = "#16191e"
PROMPT = "\x1b[38;2;55;196;240m❯\x1b[0m "


def color(value: str, fallback: str) -> str:
    if value == "default":
        return fallback
    if value in PALETTE:
        return PALETTE[value]
    return f"#{value}" if len(value) == 6 else fallback


def screen_html(screen: pyte.Screen, rows: int, cursor: bool) -> str:
    lines = []
    for y in range(rows):
        row, parts, x = screen.buffer[y], [], 0
        while x < screen.columns:
            ch = row[x]
            fg, bg = color(ch.fg, PALETTE["default"]), color(ch.bg, "transparent")
            if ch.reverse:
                fg, bg = (bg if bg != "transparent" else BG), fg
            style = f"color:{fg};background:{bg}"
            if ch.bold:
                style += ";font-weight:700"
            text = ch.data
            if cursor and y == screen.cursor.y and x == screen.cursor.x:
                style = f"color:{BG};background:#37c4f0"
                text = text if text.strip() else " "
            parts.append(f'<span style="{style}">{html.escape(text)}</span>')
            x += 1
        lines.append("".join(parts))
    return "\n".join(lines)


PAGE = """<!doctype html><html><head><meta charset="utf-8"><style>
html,body{margin:0;background:%(bg)s}
.win{background:%(bg)s;width:max-content;border:1px solid #2a2f37}
.bar{height:30px;display:flex;align-items:center;justify-content:center;border-bottom:1px solid #23272e;
     font:500 11.5px Inter,"SF Pro Text",-apple-system,Helvetica,sans-serif;letter-spacing:.02em;color:#6b7280}
pre{margin:0;padding:16px 22px 20px;font:13px/1.3 "JetBrains Mono","SF Mono",Menlo,monospace;color:#d4dae3;
    -webkit-font-smoothing:antialiased}
pre span{white-space:pre}
</style></head><body><div class="win" id="win"><div class="bar">%(title)s</div>
<pre id="term"></pre></div></body></html>"""


def build_frames(steps: list[tuple[dict, str]], speed: float, hold: float):
    """Return (duration_seconds, screen_html) pairs, typing each command first."""
    cols = steps[0][0]["cols"]
    # Tall enough that no step scrolls an earlier one off screen; unused rows are cropped.
    rows = sum(rec["rows"] + len(cmd) // cols + 2 for rec, cmd in steps) + 2
    screen = pyte.Screen(cols, rows)
    stream = pyte.Stream(screen)
    frames = []

    def snap(duration: float, cursor: bool = True) -> None:
        frames.append([duration, screen_html(screen, rows, cursor), max_row(screen)])

    stream.feed(PROMPT)
    snap(0.8)
    for n, (rec, command) in enumerate(steps):
        if n:
            stream.feed(PROMPT)
            snap(1.2)
        # Long commands type faster, so no command takes more than ~2.5s to appear.
        per_frame = max(1, round(len(command) / 80))
        for start in range(0, len(command), per_frame):
            stream.feed(command[start : start + per_frame])
            snap(min(0.03 * per_frame, 2.5 * per_frame / len(command)))
        snap(0.5)
        stream.feed("\r\n")
        chunks, i, t, step = rec["chunks"], 0, 0.0, 1 / FPS
        end = chunks[-1][0] if chunks else 0
        while t <= end + step:
            while i < len(chunks) and chunks[i][0] <= t:
                stream.feed(chunks[i][1])
                i += 1
            snap(step / speed, cursor=False)
            t += step
    stream.feed(PROMPT)
    snap(hold)
    used = max(f[2] for f in frames) + 1
    return [(d, "\n".join(h.split("\n")[:used])) for d, h, _ in frames]


def max_row(screen: pyte.Screen) -> int:
    rows = [
        y for y in range(screen.lines) if "".join(c.data for c in screen.buffer[y].values()).strip()
    ]
    return max(rows + [screen.cursor.y])


def encode_gif(frame_dir: Path, durations: list[float], out: Path) -> None:
    """Quantize every frame to one shared palette so colours stay stable."""
    frames = [Image.open(frame_dir / f"f{n:04d}.png").convert("RGB") for n in range(len(durations))]
    width = max(f.width for f in frames)
    height = max(f.height for f in frames)
    sheet = Image.new("RGB", (width, height * len(frames)))
    for n, frame in enumerate(frames):
        sheet.paste(frame, (0, n * height))
    palette = sheet.quantize(colors=255, method=Image.Quantize.MEDIANCUT)
    canvas = []
    for frame in frames:
        full = Image.new("RGB", (width, height), BG)
        full.paste(frame, (0, 0))
        canvas.append(full.quantize(palette=palette, dither=Image.Dither.NONE))
    canvas[0].save(
        out,
        save_all=True,
        append_images=canvas[1:],
        loop=0,
        optimize=False,
        duration=[max(20, round(d * 1000)) for d in durations],
        disposal=1,
    )


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("out")
    ap.add_argument("--step", nargs=2, action="append", required=True, metavar=("REC", "CMD"))
    ap.add_argument("--speed", type=float, default=1.0)
    ap.add_argument("--hold", type=float, default=4.0)
    ap.add_argument("--title", default="node-wire")
    args = ap.parse_args()
    steps = [(json.loads(Path(rec).read_text()), cmd) for rec, cmd in args.step]
    # Machine-specific temp paths (log file locations) read as noise in docs.
    tmpdir = os.environ.get("TMPDIR", "").rstrip("/")
    if tmpdir:
        for rec, _ in steps:
            for chunk in rec["chunks"]:
                chunk[1] = chunk[1].replace(tmpdir, "$TMPDIR")
    frames = build_frames(steps, args.speed, args.hold)

    merged = []
    for duration, body in frames:
        if merged and merged[-1][1] == body:
            merged[-1][0] += duration
        else:
            merged.append([duration, body])

    tmp = Path(tempfile.mkdtemp())
    with sync_playwright() as p:
        browser = p.chromium.launch(executable_path=os.environ.get("CHROMIUM_PATH"))
        page = browser.new_page(device_scale_factor=2)
        page.set_content(PAGE % {"bg": BG, "title": html.escape(args.title)})
        win = page.locator("#win")
        for n, (_, body) in enumerate(merged):
            page.evaluate("b => document.getElementById('term').innerHTML = b", body)
            win.screenshot(path=str(tmp / f"f{n:04d}.png"))
        browser.close()

    encode_gif(tmp, [d for d, _ in merged], Path(args.out))
    if not os.environ.get("KEEP_FRAMES"):
        shutil.rmtree(tmp)
    else:
        print(tmp)
    print(f"{args.out}: {len(merged)} frames")


if __name__ == "__main__":
    main()
