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
    "default": "#e6edf3",
    "black": "#484f58",
    "red": "#ff7b72",
    "green": "#3fb950",
    "brown": "#d29922",
    "yellow": "#d29922",
    "blue": "#58a6ff",
    "magenta": "#bc8cff",
    "cyan": "#39c5cf",
    "white": "#b1bac4",
    "brightblack": "#6e7681",
    "brightred": "#ffa198",
    "brightgreen": "#56d364",
    "brightbrown": "#e3b341",
    "brightyellow": "#e3b341",
    "brightblue": "#79c0ff",
    "brightmagenta": "#d2a8ff",
    "brightcyan": "#56d4dd",
    "brightwhite": "#ffffff",
}
BG = "#0d1117"
PROMPT = "\x1b[38;2;63;185;80m❯\x1b[0m "


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
                style = f"color:{BG};background:#e6edf3"
                text = text if text.strip() else " "
            parts.append(f'<span style="{style}">{html.escape(text)}</span>')
            x += 1
        lines.append("".join(parts))
    return "\n".join(lines)


PAGE = """<!doctype html><html><head><meta charset="utf-8"><style>
html,body{margin:0;background:%(bg)s}
.win{background:%(bg)s;border:1px solid #30363d;width:max-content}
.bar{height:34px;background:#161b22;border-bottom:1px solid #30363d;display:flex;align-items:center;padding:0 14px;gap:8px;font:13px -apple-system,Helvetica,sans-serif;color:#8b949e}
.dot{width:12px;height:12px;border-radius:50%%;display:inline-block}
.title{flex:1;text-align:center;margin-right:60px}
pre{margin:0;padding:14px 18px 18px;font:14px/1.32 Menlo,"SF Mono",monospace;color:#e6edf3}
pre span{white-space:pre}
</style></head><body><div class="win" id="win"><div class="bar">
<span class="dot" style="background:#ff5f57"></span><span class="dot" style="background:#febc2e"></span>
<span class="dot" style="background:#28c840"></span><span class="title">%(title)s</span></div>
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
    ap.add_argument("--title", default="node-wire — zsh")
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
        page = browser.new_page(device_scale_factor=1)
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
