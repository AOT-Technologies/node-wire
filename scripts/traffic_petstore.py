#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 AOT Technologies
#
# SPDX-License-Identifier: Apache-2.0

"""Send a stream of tool calls through ToolHive to the Petstore MCP host, for its logs and traces.

Local only, a companion to scripts/e2e_toolhive.py (which builds the ``pet-store-nw-mcp:latest``
image this deploys). Its own ToolHive workload, ``pet-store-nw-mcp-traffic``, calls the public
Petstore demo API (petstore.swagger.io, which accepts any key) with a weighted mix:

  - successes: find pets by status or tags, the store inventory, a pet that exists;
  - upstream failures: a pet, order or user that doesn't exist (``HTTP_STATUS_ERROR``, a 404);
  - failures before any upstream call: bad arguments (``VALIDATION_ERROR``), an unknown tool
    (``UNKNOWN_TOOL``).

Only read-only tools are called: it's a shared public demo. With ``--otlp-endpoint`` the logs,
traces and metrics land in the grafana/ stack (service ``nw-pet_store``), for the Connector
Calls & Logs dashboard. It ends with the calls by tool and outcome.

Usage:
  uv run python scripts/traffic_petstore.py --otlp-endpoint http://host.docker.internal:4318
  uv run python scripts/traffic_petstore.py --calls 50 --rate 1 --otlp-endpoint ...
  uv run python scripts/traffic_petstore.py --duration 600 --otlp-endpoint ...
  uv run python scripts/traffic_petstore.py --clean     # remove the workload
"""

from __future__ import annotations

import argparse
import collections
import json
import random
import sys
import time
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional

import e2e_toolhive as e2e

WORKLOAD = "pet-store-nw-mcp-traffic"
IMAGE = "pet-store-nw-mcp:latest"
PORT = 18086  # clear of the e2e scenarios' ports (e2e_toolhive.HOST_PORTS)
# The Petstore demo accepts any value for either auth scheme.
DEMO_CREDENTIALS = {"PET_STORE_API_KEY": "special-key", "PET_STORE_ACCESS_TOKEN": "special-key"}
MISSING_ID = 987654321987  # no pet or order has it


@dataclass(frozen=True)
class Call:
    kind: str  # what the call is meant to show, e.g. "ok", "upstream 404", "bad arguments"
    tool: str
    arguments: Dict[str, Any]


def _mix(pet_ids: List[int], rng: random.Random) -> List[tuple[int, Callable[[], Call]]]:
    """(weight, call) pairs: mostly successes, some upstream 404s, a few rejected calls."""
    statuses = ["available", "pending", "sold"]
    return [
        (
            25,
            lambda: Call("ok", "pet_store_find_pets_by_status", {"status": [rng.choice(statuses)]}),
        ),
        (15, lambda: Call("ok", "pet_store_get_inventory", {})),
        (20, lambda: Call("ok", "pet_store_get_pet_by_id", {"petid": rng.choice(pet_ids)})),
        (5, lambda: Call("ok", "pet_store_find_pets_by_tags", {"tags": ["string"]})),
        (10, lambda: Call("upstream 404", "pet_store_get_pet_by_id", {"petid": MISSING_ID})),
        (8, lambda: Call("upstream 404", "pet_store_get_order_by_id", {"orderid": MISSING_ID})),
        (
            5,
            lambda: Call(
                "upstream 404", "pet_store_get_user_by_name", {"username": "no-such-user"}
            ),
        ),
        (7, lambda: Call("bad arguments", "pet_store_get_pet_by_id", {"petid": "not-a-number"})),
        (5, lambda: Call("unknown tool", "pet_store_adopt_pet", {})),
    ]


def _known_pet_ids(url: str) -> List[int]:
    """Ids of pets the demo has now (they come and go: other people use it too)."""
    failed, text = e2e.call_tool(url, "pet_store_find_pets_by_status", {"status": ["available"]})
    if failed:
        raise RuntimeError(f"cannot list pets: {text[:300]}")
    body = json.loads(text).get("data", {}).get("body") or []
    ids = [pet["id"] for pet in body if isinstance(pet, dict) and isinstance(pet.get("id"), int)]
    return ids[:50] or [1]


def deploy(otlp_endpoint: Optional[str]) -> str:
    """Start the workload; returns its ToolHive proxy URL once it lists its tools."""
    env = {**DEMO_CREDENTIALS}
    if otlp_endpoint:
        env["OTEL_EXPORTER_OTLP_ENDPOINT"] = otlp_endpoint
    e2e.run_host_workload(WORKLOAD, IMAGE, env, port=PORT)
    url = e2e.workload_url(WORKLOAD)
    e2e.listed_tools(WORKLOAD)  # waits until the server answers through the proxy
    return url


def run(url: str, *, calls: int, duration: float, rate: float, seed: int) -> collections.Counter:
    rng = random.Random(seed)
    pet_ids = _known_pet_ids(url)
    mix = _mix(pet_ids, rng)
    weights = [weight for weight, _ in mix]
    outcomes: collections.Counter = collections.Counter()
    latencies: Dict[str, List[float]] = collections.defaultdict(list)
    started = time.monotonic()
    sent = 0
    while (calls and sent < calls) or (duration and time.monotonic() - started < duration):
        call = rng.choices(mix, weights=weights)[0][1]()
        t0 = time.monotonic()
        try:
            failed, text = e2e.call_tool(url, call.tool, call.arguments)
            code = (e2e.taxonomy(text).code or "?") if failed else "ok"
        except Exception as exc:  # noqa: BLE001 — a broken call is an outcome too
            code = f"transport: {type(exc).__name__}"
        took = time.monotonic() - t0
        outcomes[(call.tool, call.kind, code)] += 1
        latencies[code].append(took)
        sent += 1
        if sent % 25 == 0:
            ok = sum(n for (_, _, c), n in outcomes.items() if c == "ok")
            print(f"  {sent} calls, {ok} ok, {time.monotonic() - started:.0f}s", flush=True)
        time.sleep(max(0.0, 1 / rate - took))
    _report(outcomes, latencies, time.monotonic() - started)
    return outcomes


def _report(
    outcomes: collections.Counter, latencies: Dict[str, List[float]], elapsed: float
) -> None:
    total = sum(outcomes.values())
    print(f"\n{total} calls in {elapsed:.0f}s\n")
    print(f"  {'tool':34s} {'meant as':14s} {'outcome':24s} {'calls':>5s}")
    for (tool, kind, code), n in sorted(outcomes.items(), key=lambda item: -item[1]):
        print(f"  {tool:34s} {kind:14s} {code:24s} {n:5d}")
    print("\n  by outcome (median latency):")
    for code, values in sorted(latencies.items(), key=lambda item: -len(item[1])):
        median = sorted(values)[len(values) // 2]
        print(f"  {code:24s} {len(values):5d}   {median * 1000:6.0f} ms")
    surprises = [key for key in outcomes if (key[1] == "ok") != (key[2] == "ok")]
    for tool, kind, code in surprises:
        print(f"  note: {tool} ({kind}) returned {code}: the shared demo's data changes")


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--calls", type=int, default=200, help="stop after this many (default 200)")
    parser.add_argument(
        "--duration", type=float, default=0, help="or: stop after this many seconds"
    )
    parser.add_argument(
        "--rate", type=float, default=2.0, help="calls per second at most (default 2)"
    )
    parser.add_argument("--seed", type=int, default=7, help="for a repeatable mix")
    parser.add_argument(
        "--otlp-endpoint",
        metavar="URL",
        help="OTLP/HTTP collector as the container reaches it, e.g. "
        "http://host.docker.internal:4318 (the grafana/ stack)",
    )
    parser.add_argument("--clean", action="store_true", help="remove the workload and stop")
    args = parser.parse_args(argv)

    if args.clean:
        e2e._remove_workload(WORKLOAD)
        print(f"Removed {WORKLOAD}.")
        return 0
    problems = e2e.preflight(build=False)
    if e2e._cmd("docker", "image", "inspect", IMAGE, check=False).returncode:
        problems.append(f"no {IMAGE} image: run scripts/e2e_toolhive.py first")
    if problems:
        for problem in problems:
            print(f"error: {problem}", file=sys.stderr)
        return 1

    calls = 0 if args.duration else args.calls
    print(f"Deploying {WORKLOAD} (telemetry: {args.otlp_endpoint or 'off'})…")
    url = deploy(args.otlp_endpoint)
    limit = f"{args.duration:.0f}s" if args.duration else f"{calls} calls"
    print(f"Calling it through ToolHive ({url}), {limit} at up to {args.rate}/s…")
    run(url, calls=calls, duration=args.duration, rate=args.rate, seed=args.seed)
    print(f"\nLeft running: {WORKLOAD}. In Grafana pick Service nw-pet_store; --clean removes it.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
