# SPDX-FileCopyrightText: 2026 AOT Technologies
# SPDX-License-Identifier: Apache-2.0
"""Write the animated SVG diagrams in docs/images/media/ from one shared style.

Usage: python diagrams.py [OUT_DIR]

Style: white canvas, hairline boxes, one accent colour (brand blue) used only for
motion and the active state, brand pink only on error paths. Animations are CSS,
so they play inside <img> on GitHub and MkDocs, and stop under reduced motion.
"""

from __future__ import annotations

import re
import sys
from html import escape
from pathlib import Path

INK = "#111827"
MUTED = "#6b7280"
FAINT = "#9ca3af"
LINE = "#d1d5db"
HAIR = "#e5e7eb"
SURFACE = "#f9fafb"
ACCENT = "#1a9fc8"
ACCENT_SOFT = "#e8f6fb"
ERROR = "#e01d5a"
# One colour per transport, for traffic in the hero. Brand pink stays reserved for errors.
TRANSPORT = {"REST": ACCENT, "gRPC": "#c9971c", "MCP": "#12867a"}

SANS = "Inter, 'SF Pro Text', -apple-system, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif"
MONO = "'JetBrains Mono', 'SF Mono', ui-monospace, Menlo, Consolas, monospace"

STYLE = f"""
    text {{ font-family: {SANS}; fill: {INK}; }}
    .t {{ font-size: 13px; font-weight: 600; letter-spacing: -0.01em; }}
    .b {{ font-size: 11.5px; fill: {MUTED}; }}
    .k {{ font-size: 10px; font-weight: 600; fill: {FAINT}; letter-spacing: 0.08em; }}
    .m {{ font-family: {MONO}; font-size: 11px; fill: {MUTED}; }}
    .mi {{ font-family: {MONO}; font-size: 11px; fill: {INK}; }}
    .ln {{ fill: none; stroke: {LINE}; stroke-width: 1; }}
    .er {{ fill: none; stroke: {ERROR}; stroke-width: 1; stroke-opacity: .45; }}
    .route {{ offset-rotate: 0deg; opacity: 0; }}
    .hl {{ opacity: 0; animation: hl var(--d, 6s) ease-in-out infinite; animation-delay: var(--w, 0s); }}
    .in {{ opacity: 0; animation: fadein var(--d, 8s) ease-out infinite; animation-delay: var(--w, 0s); }}
    @keyframes hl {{ 0%, 22%, 100% {{ opacity: 0; }} 4%, 16% {{ opacity: 1; }} }}
    @keyframes fadein {{ 0% {{ opacity: 0; transform: translateY(4px); }} 8%, 82% {{ opacity: 1; transform: none; }}
                        92%, 100% {{ opacity: 0; }} }}
    .bar {{ transform-box: fill-box; transform-origin: left; animation: grow 8s ease-in-out infinite; }}
    @keyframes grow {{ 0% {{ transform: scaleX(0); }} 70%, 90% {{ transform: scaleX(1); }} 100% {{ transform: scaleX(0); }} }}
    @media (prefers-reduced-motion: reduce) {{
      .hl, .route {{ animation: none !important; opacity: 0; }}
      .in, .bar {{ animation: none; opacity: 1; }}
    }}
"""


def path_length(d: str) -> float:
    """Length of an absolute path made of M, L and C commands (curves are sampled)."""
    tokens = re.findall(r"[MLC]|-?[0-9.]+", d)
    length, x, y, i, cmd = 0.0, 0.0, 0.0, 0, "M"
    while i < len(tokens):
        if tokens[i] in "MLC":
            cmd = tokens[i]
            i += 1
        n = 6 if cmd == "C" else 2
        v = [float(t) for t in tokens[i : i + n]]
        i += n
        if cmd == "M":
            x, y = v
        elif cmd == "L":
            length += ((v[0] - x) ** 2 + (v[1] - y) ** 2) ** 0.5
            x, y = v
        else:
            (x1, y1, x2, y2, x3, y3), px, py = v, x, y
            for k in range(1, 33):
                t = k / 32
                u = 1 - t
                qx = u**3 * x + 3 * u * u * t * x1 + 3 * u * t * t * x2 + t**3 * x3
                qy = u**3 * y + 3 * u * u * t * y1 + 3 * u * t * t * y2 + t**3 * y3
                length += ((qx - px) ** 2 + (qy - py) ** 2) ** 0.5
                px, py = qx, qy
            x, y = x3, y3
    return length


class Svg:
    def __init__(self, width: int, height: int, title: str, desc: str) -> None:
        self.w, self.h = width, height
        self.parts: list[str] = []
        # REUSE-IgnoreStart
        self.head = (
            f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {width} {height}" '
            f'width="{width}" height="{height}" role="img" aria-labelledby="t d">\n'
            "  <!--\n  SPDX-FileCopyrightText: 2026 AOT Technologies\n"
            "  SPDX-License-Identifier: Apache-2.0\n  -->\n"
            f'  <title id="t">{escape(title)}</title>\n'
            f'  <desc id="d">{escape(desc)}</desc>\n'
            f"  <style>{STYLE}  </style>\n"
            f'  <rect width="{width}" height="{height}" rx="12" fill="#ffffff"/>\n'
            f'  <rect x=".5" y=".5" width="{width - 1}" height="{height - 1}" rx="12" '
            f'fill="none" stroke="{HAIR}"/>\n'
        )
        # REUSE-IgnoreEnd

    def add(self, raw: str) -> None:
        self.parts.append("  " + raw)

    def text(self, x, y, s, cls="b", anchor="start", fill=None, extra="") -> None:
        f = f' fill="{fill}"' if fill else ""
        self.add(
            f'<text x="{x}" y="{y}" class="{cls}" text-anchor="{anchor}"{f}{extra}>{escape(s)}</text>'
        )

    def label(self, x, y, s, anchor="start") -> None:
        self.text(x, y, s.upper(), "k", anchor)

    def box(
        self,
        x,
        y,
        w,
        h,
        title=None,
        sub=None,
        mono_sub=False,
        fill="#ffffff",
        stroke=HAIR,
        center=False,
        title_mono=False,
    ) -> None:
        self.add(
            f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="7" fill="{fill}" stroke="{stroke}"/>'
        )
        tx, anchor = (x + w / 2, "middle") if center else (x + 14, "start")
        if title and sub:
            self.text(tx, y + h / 2 - 3, title, "mi" if title_mono else "t", anchor)
            self.text(tx, y + h / 2 + 13, sub, "m" if mono_sub else "b", anchor)
        elif title:
            self.text(tx, y + h / 2 + 4.5, title, "mi" if title_mono else "t", anchor)

    def line(self, d, cls="ln") -> None:
        self.add(f'<path class="{cls}" d="{d}"/>')

    def route(self, d, period, delay, speed=240.0, color=ACCENT, r=3) -> None:
        """A dot that travels a polyline at a constant speed, once per shared period.

        Every route uses the same speed, so a dot looks the same in every element it
        crosses; the shared period keeps several routes evenly staggered.
        """
        travel = path_length(d) / speed
        end = min(travel / period, 1.0) * 100
        name = f"r{len(self.parts)}"
        self.add(
            f"<style>@keyframes {name} {{ 0% {{ offset-distance: 0%; opacity: 0; }} "
            f"1% {{ opacity: 1; }} {end - 1:.2f}% {{ opacity: 1; }} "
            f"{end:.2f}% {{ offset-distance: 100%; opacity: 0; }} "
            f"100% {{ offset-distance: 100%; opacity: 0; }} }}</style>"
        )
        self.add(
            f'<circle class="route" r="{r}" fill="{color}" '
            f"style=\"offset-path: path('{d}'); animation: {name} {period}s linear "
            f'{delay}s infinite"/>'
        )

    def highlight(self, x, y, w, h, dur, delay) -> None:
        """Accent tint + left rule that fades in for a moment: the 'active' state."""
        self.add(
            f'<g class="hl" style="--d: {dur}s; --w: {delay}s">'
            f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="7" fill="{ACCENT_SOFT}" stroke="{ACCENT}" stroke-opacity=".5"/>'
            f"</g>"
        )

    def write(self, path: Path) -> None:
        path.write_text(self.head + "\n".join(self.parts) + "\n</svg>\n")


def hero() -> Svg:
    """Request path, matching the layer diagram in docs/architecture.md."""
    s = Svg(
        1080,
        360,
        "Node Wire at a glance",
        "A caller reaches a binding: REST, gRPC or MCP. The binding resolves the tenant, and "
        "ConnectorFactory resolves that tenant's named config and returns a tenant-pinned "
        "connector instance with its secrets, auth and policy. BaseConnector.run checks the tenant pin, then validates, applies scope policy, "
        "retries behind a circuit breaker and maps errors inside an OpenTelemetry span. The "
        "connector action calls the external API, and run returns the same ConnectorResponse "
        "through every transport. Traffic dots are blue for REST, amber for gRPC and teal for "
        "MCP; the returning response is grey.",
    )
    s.add(
        f'<defs><marker id="a" viewBox="0 0 8 8" refX="7" refY="4" markerWidth="7" markerHeight="7" '
        f'orient="auto-start-reverse"><path d="M0 0 L8 4 L0 8 z" fill="{LINE}"/></marker></defs>'
    )

    def arrow(d: str, cls: str = "ln") -> None:
        s.add(f'<path class="{cls}" d="{d}" marker-end="url(#a)"/>')

    def group(x: int, w: int, name: str, layer: str) -> None:
        s.add(
            f'<rect x="{x}" y="28" width="{w}" height="260" rx="10" fill="{SURFACE}" stroke="{HAIR}"/>'
        )
        s.label(x + 14, 50, f"{name} · {layer}")

    # Layering, back to front: group panels, traffic dots, then element boxes and arrows.
    # Element boxes are opaque, so a dot is hidden while it is inside one.
    group(168, 312, "Bindings", "Layer C")
    group(504, 248, "Runtime", "Layer A")
    group(776, 160, "Connectors", "Layer B")

    # Request path: one route per binding, through the centre line of every element
    period = 6.0
    for i, (name, start, y) in enumerate(
        (("REST", 121, 101), ("gRPC", 121, 173), ("MCP", 221, 245))
    ):
        s.route(
            f"M142 {start} L 182 {y} L 399 {y} L 399 111 L 1008 111",
            period,
            i * period / 3,
            color=TRANSPORT[name],
        )
    # Response path: run() returns the envelope back through the binding to the caller.
    # Neutral, because the same envelope goes back on every transport.
    s.route("M628 288 L 628 320 L 83 320 L 83 246", period, 3.4, color=MUTED)

    # Callers
    s.label(24, 50, "Callers")
    s.box(24, 96, 118, 50, "Apps", "services")
    s.box(24, 196, 118, 50, "AI agents", "MCP clients")

    # Layer C: bindings
    for y, t, sub in (
        (76, "REST", "tenant: header, JWT"),
        (148, "gRPC", "tenant: metadata"),
        (220, "MCP", "tenant: session"),
    ):
        s.box(182, y, 124, 50, t, sub)
        # Key: the dot colour this transport's traffic uses
        s.add(f'<circle cx="292" cy="{y + 16}" r="3" fill="{TRANSPORT[t]}"/>')
    s.box(326, 76, 146, 194)
    s.text(340, 100, "invoke.py", "mi")
    s.text(340, 120, "ConnectorFactory", "t")
    for i, line in enumerate(
        ("get(tenant, config)", "named config", "secrets · auth", "PolicyHook", "→ pinned instance")
    ):
        s.text(340, 146 + i * 18, line, "m" if i == 0 else "b")

    # Layer A: runtime
    s.text(518, 82, "BaseConnector.run()", "mi")
    s.box(518, 94, 220, 28)
    s.highlight(518, 94, 220, 28, 7, 0.9)
    s.text(530, 112, "Tenant pin check", "b", fill=INK)
    s.line("M524 142 L 518 142 L 518 278 L 524 278")
    s.text(530, 146, "OpenTelemetry span", "k")
    for i, step in enumerate(("Validation", "Scope policy", "Retries · breaker", "Error mapping")):
        y = 156 + i * 31
        s.box(530, y, 208, 26)
        s.highlight(530, y, 208, 26, 7, 1.3 + i * 0.4)
        s.text(542, y + 17, step, "b", fill=INK)

    # Layer B: connectors
    # Same height as the ConnectorFactory box, so the column ends level with Layer C
    s.box(790, 76, 132, 194)
    s.highlight(790, 76, 132, 194, 7, 3.0)
    s.text(808, 102, "Connector", "t")
    s.text(808, 120, "@nw_action", "m")
    s.text(808, 156, "Generated", "t")
    s.text(808, 174, "nw gen-all", "m")
    s.text(808, 190, "from OpenAPI", "b")

    # External
    s.label(960, 50, "External")
    s.box(960, 86, 96, 50, "APIs", "SaaS · FHIR")

    # Request path arrows
    arrow("M142 121 L 182 101")
    arrow("M142 121 L 182 173")
    arrow("M142 221 L 182 245")
    for y in (101, 173, 245):
        arrow(f"M306 {y} L 326 {y}")
    arrow("M472 111 L 518 111")
    arrow("M738 111 L 790 111")
    arrow("M922 111 L 960 111")

    # Response path arrow
    arrow("M628 288 L 628 320 L 83 320 L 83 246")
    s.text(360, 340, "ConnectorResponse · same envelope on every transport", "m", "middle")
    return s


def run_pipeline() -> Svg:
    s = Svg(
        960,
        250,
        "The run() pipeline",
        "Every action runs the tenant pin check, Pydantic validation, the PolicyHook, retries with "
        "a circuit breaker, then the connector's internal_execute. A failure at any step goes to "
        "the ErrorMapper. Both paths end in one ConnectorResponse. Everything after the tenant "
        "check runs inside an OpenTelemetry span.",
    )
    steps = [
        ("Tenant", "pin check", 146, 96),
        ("Validate", "Pydantic", 264, 96),
        ("Policy", "PolicyHook", 382, 96),
        ("Resilience", "retries · breaker", 500, 128),
        ("Execute", "connector", 652, 118),
    ]
    speed, period, start = 240.0, 7.0, 78

    # Layering, back to front: lines, traffic dots, then boxes (which hide a dot inside them).
    s.label(40, 40, "Every action, every transport")
    s.line("M244 64 L 244 58 L 770 58 L 770 64")
    s.text(507, 52, "OpenTelemetry span", "k", "middle")
    s.line("M124 100 L 790 100")
    for _, _, x, w in steps:
        s.line(f"M{x + w / 2} 124 L {x + w / 2} 176", "er")
    s.line("M770 196 L 860 196 L 860 120", "er")
    s.route(f"M{start} 100 L 860 100", period, 0, speed)
    # A failure at validation, shown after the success run has finished
    s.route("M312 100 L 312 196 L 860 196 L 860 100", period, 3.6, speed, ERROR)

    s.box(32, 80, 92, 40, "run()", center=True, title_mono=True)
    for t, sub, x, w in steps:
        s.box(x, 76, w, 48)
        # Light each step as the success dot reaches it
        arrive = (x + w / 2 - start) / speed
        s.highlight(x, 76, w, 48, period, max(arrive - 0.35, 0))
        s.text(x + w / 2, 96, t, "t", "middle")
        s.text(x + w / 2, 112, sub, "b", "middle")
    s.box(790, 80, 140, 40, "ConnectorResponse", center=True, title_mono=True)
    s.box(160, 176, 610, 40)
    s.text(176, 200, "ErrorMapper", "t")
    s.text(270, 200, "stable error_code · RETRYABLE · BUSINESS · AUTH · FATAL", "m")
    return s


def tenancy() -> Svg:
    s = Svg(
        960,
        250,
        "Tenant-pinned connectors",
        "Requests from tenants acme and globex reach ConnectorFactory, which resolves each tenant's "
        "own config and secrets from the config store and returns an instance pinned to that "
        "tenant. A tenant with no config, initech, is refused.",
    )
    # Layering, back to front: lines, traffic dots, then boxes (which hide a dot inside them).
    for y in (81, 145, 209):
        s.line(f"M220 {y} C 305 {y}, 305 125, 390 125")
    s.line("M570 125 C 655 125, 655 81, 740 81")
    s.line("M570 125 L 740 145")
    s.line("M570 125 C 655 125, 655 209, 740 209", "er")
    s.line("M480 154 L 480 184")
    period = 6.0
    for i, (y, out, color) in enumerate(
        (
            (81, "C 655 125, 655 81, 740 81 L 835 81", ACCENT),
            (145, "L 740 145 L 835 145", ACCENT),
            (209, "C 655 125, 655 209, 740 209 L 835 209", ERROR),
        )
    ):
        s.route(
            f"M130 {y} L 220 {y} C 305 {y}, 305 125, 390 125 L 570 125 {out}",
            period,
            i * period / 3,
            color=color,
        )

    s.label(40, 40, "Callers")
    s.box(40, 56, 180, 50, "acme", "X-Tenant-ID header")
    s.box(40, 120, 180, 50, "globex", "JWT tenant claim")
    s.box(40, 184, 180, 50, "initech", "no config")
    s.label(390, 40, "Resolve")
    s.box(
        390,
        96,
        180,
        58,
        "ConnectorFactory",
        "get(id, tenant_id)",
        mono_sub=True,
        fill=SURFACE,
        stroke=LINE,
    )
    s.box(400, 184, 160, 50, "Config store", "configs · secrets")
    s.label(740, 40, "Instances")
    s.box(740, 56, 190, 50, "Pinned to acme", "acme's credentials only")
    s.box(740, 120, 190, 50, "Pinned to globex", "globex's credentials only")
    s.box(740, 184, 190, 50, "Refused", "fails closed · 403", stroke="#f3c0cf")
    return s


def cli_pipeline() -> Svg:
    s = Svg(
        960,
        220,
        "The nw CLI pipeline",
        "nw gen-all takes an OpenAPI or Swagger spec through four stages: connector codegen, wheel "
        "build, MCP host build and wiring into config. nw docker-build then builds a Docker image "
        "from the MCP host. gen-whl and gen-mcp run single stages; gen-stacklok builds a stacklok "
        "MCP server.",
    )
    s.box(32, 74, 110, 52, "OpenAPI", "path or URL", center=True)
    s.text(176, 46, "nw gen-all", "mi")
    s.line("M176 56 L 770 56")
    s.add(f'<rect class="bar" x="176" y="55" width="594" height="2" fill="{ACCENT}"/>')
    stages = [
        ("Codegen", "schema · logic"),
        ("Wheels", "build-packages.sh"),
        ("MCP host", "nw-mcp-builder"),
        ("Wire", "connectors.yaml"),
    ]
    for i, (t, sub) in enumerate(stages):
        x = 176 + i * 152
        s.box(x, 74, 138, 52)
        s.highlight(x, 74, 138, 52, 8, 0.2 + i * 1.4)
        s.text(x + 69, 97, t, "t", "middle")
        s.text(x + 69, 113, sub, "b", "middle")
    s.line("M142 100 L 176 100")
    for i in range(3):
        x = 176 + i * 152 + 138
        s.line(f"M{x} 100 L {x + 14} 100")
    s.line("M770 100 L 800 100")
    s.box(800, 74, 130, 52, "nw docker-build", "MCP server image", center=True, title_mono=True)
    s.label(176, 162, "Single stages")
    for x, w, t in (
        (176 + 152, 138, "nw gen-whl"),
        (176 + 304, 138, "nw gen-mcp"),
        (176 + 456, 274, "nw gen-stacklok · curated tools"),
    ):
        s.box(x, 150, w, 30, t, center=True, title_mono=True)
    s.text(
        480,
        204,
        "Each stage reports in a Rich progress view; a failure names the stage and the fix.",
        "b",
        "middle",
    )
    return s


def connector_anatomy() -> Svg:
    s = Svg(
        960,
        270,
        "Anatomy of a connector",
        "A connector package holds schema.py with Pydantic models keyed by an action field, and "
        "logic.py with a BaseConnector subclass whose @nw_action methods call the external system. "
        "auto_register loads it through the node_wire.connectors entry point, which also registers "
        "its error_map. Each action then becomes a REST route, an MCP tool and a gRPC Invoke call.",
    )
    outs = [
        ("REST route", "POST /connectors/<id>/send", "REST"),
        ("gRPC Invoke", 'action: "send"', "gRPC"),
        ("MCP tool", "<id>_send", "MCP"),
    ]
    # Layering, back to front: lines, traffic dots, then boxes (which hide a dot inside them).
    s.line("M372 150 L 440 150")
    period = 6.0
    for i, (_, _, transport) in enumerate(outs):
        y = 81 + i * 64
        s.line(f"M630 150 C 675 150, 675 {y}, 720 {y}")
        s.route(
            f"M372 150 L 535 150 L 630 150 C 675 150, 675 {y}, 720 {y} L 825 {y}",
            period,
            i * period / 3,
            color=TRANSPORT[transport],
        )

    s.label(32, 40, "Package")
    s.text(32, 62, "src/node_wire_<id>/", "mi")
    s.box(32, 76, 340, 72)
    s.highlight(32, 76, 340, 72, 6, 0)
    s.text(46, 98, "schema.py", "t")
    s.add('<text x="46" y="118" class="m" xml:space="preserve">class SendInput(BaseModel):</text>')
    s.add(
        '<text x="46" y="134" class="m" xml:space="preserve">    action: Literal["send"] = "send"</text>'
    )
    s.box(32, 158, 340, 72)
    s.highlight(32, 158, 340, 72, 6, 1.2)
    s.text(46, 180, "logic.py", "t")
    s.add('<text x="46" y="200" class="m" xml:space="preserve">@nw_action("send")</text>')
    s.add(
        '<text x="46" y="216" class="m" xml:space="preserve">async def send(self, params, *, trace_id)</text>'
    )
    s.text(32, 252, "Optional: action_spec.py · normalizers.py · error_map", "b")

    s.label(440, 40, "Register")
    s.box(440, 112, 190, 76, fill=SURFACE, stroke=LINE)
    s.text(456, 138, "auto_register()", "mi")
    s.text(456, 156, "entry point node_wire.connectors", "b")
    s.text(456, 172, "registry + scoped error_map", "b")

    s.label(720, 40, "Exposed as")
    for i, (t, sub, transport) in enumerate(outs):
        y = 56 + i * 64
        s.box(720, y, 210, 50, t, sub, mono_sub=True)
        s.add(f'<circle cx="916" cy="{y + 16}" r="3" fill="{TRANSPORT[transport]}"/>')
    return s


def mcp_tool_search() -> Svg:
    s = Svg(
        960,
        280,
        "MCP tool search mode",
        "In list mode, tools/list returns every tool with its schema, so the listing grows with the "
        "connector. In search mode it returns two tools. The agent calls nw_search_tools with "
        "keywords, gets the best matches with their input schemas, then runs one through "
        "nw_call_tool, which validates the arguments and returns the ConnectorResponse.",
    )
    s.label(32, 40, "List mode · default")
    s.text(32, 66, "tools/list", "mi")
    s.add(f'<rect x="32" y="78" width="320" height="8" rx="4" fill="{HAIR}"/>')
    s.text(32, 106, "Every tool and every schema, on every request.", "b")
    s.label(32, 152, "Search mode")
    s.text(32, 178, "NW_MCP_TOOL_MODE=search", "mi")
    s.add(f'<rect x="32" y="190" width="40" height="8" rx="4" fill="{ACCENT}"/>')
    s.text(32, 218, "Two tools, about 1–2 KB whatever the connector size.", "b")
    s.add(f'<line x1="400" y1="28" x2="400" y2="252" stroke="{HAIR}"/>')

    s.label(440, 40, "Agent")
    s.label(928, 40, "Node Wire MCP server", "end")
    msgs = [
        (440, 56, 330, "nw_search_tools", '{"query": "create lead"}', 0),
        (598, 108, 330, "salesforce_create_lead", "+ inputSchema, ranked by BM25", 1.6),
        (440, 160, 330, "nw_call_tool", '{"name": "salesforce_create_lead", …}', 3.2),
        (598, 212, 330, "ConnectorResponse", "same validation and scope policy", 4.8),
    ]
    for x, y, w, t, sub, delay in msgs:
        stroke = ACCENT if x == 440 else HAIR
        s.add(
            f'<g class="in" style="--d: 8s; --w: {delay}s">'
            f'<rect x="{x}" y="{y}" width="{w}" height="42" rx="7" fill="#ffffff" stroke="{stroke}" stroke-opacity="{0.6 if x == 440 else 1}"/>'
            f'<text x="{x + 14}" y="{y + 18}" class="mi">{escape(t)}</text>'
            f'<text x="{x + 14}" y="{y + 33}" class="m">{escape(sub)}</text></g>'
        )
    return s


DIAGRAMS = {
    "hero.svg": hero,
    "run-pipeline.svg": run_pipeline,
    "tenancy.svg": tenancy,
    "cli-pipeline.svg": cli_pipeline,
    "connector-anatomy.svg": connector_anatomy,
    "mcp-tool-search.svg": mcp_tool_search,
}


def main() -> None:
    out = Path(sys.argv[1] if len(sys.argv) > 1 else "docs/images/media")
    for name, build in DIAGRAMS.items():
        build().write(out / name)
        print(out / name)


if __name__ == "__main__":
    main()
