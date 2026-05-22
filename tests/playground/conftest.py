#
# SPDX-FileCopyrightText: 2026 AOT Technologies
# SPDX-License-Identifier: Apache-2.0
#
from __future__ import annotations

import os
import socket
import threading
import time

from dotenv import load_dotenv
import httpx
import pytest

# Load .env before any app imports so connectors initialise with real credentials.
load_dotenv(override=False)


@pytest.fixture(scope="session")
def browser_type_launch_args(browser_type_launch_args):
    """Override Playwright launch arguments dynamically via environment variables."""
    env_val = (
        os.getenv("PLAYGROUND_HEADED") or os.getenv("HEADED") or os.getenv("PLAYWRIGHT_HEADLESS")
    )
    is_headed = False
    if env_val:
        env_val_lower = env_val.lower().strip()
        if env_val_lower in ("true", "1", "yes"):
            is_headed = True
        elif env_val_lower in ("false", "0", "no") and os.getenv("PLAYWRIGHT_HEADLESS"):
            is_headed = True
    return {**browser_type_launch_args, "headless": not is_headed}


@pytest.fixture(scope="session")
def api_server_url():
    """Start the real FastAPI server on a free port and yield its base URL.

    The playground UI is served at /playground/ and the scenarios API at
    /scenarios/*, so browser fetch() calls with relative paths resolve
    correctly without any Playwright route interception.
    """
    import uvicorn  # noqa: PLC0415
    from bindings.rest_api.app import app as rest_app  # noqa: PLC0415

    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]

    config = uvicorn.Config(rest_app, host="127.0.0.1", port=port, log_level="error")
    server = uvicorn.Server(config)

    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()

    base = f"http://127.0.0.1:{port}"
    with httpx.Client(timeout=2) as probe:
        for _ in range(60):
            try:
                probe.get(f"{base}/health")
                break
            except Exception:
                time.sleep(0.3)
        else:
            pytest.fail("FastAPI server did not start within 18 seconds")

    yield base

    server.should_exit = True
    thread.join(timeout=5)


@pytest.fixture
def playground_page(page, api_server_url: str):
    """Navigate to the playground served by the real FastAPI server."""
    page.goto(f"{api_server_url}/playground/")
    page.wait_for_load_state("domcontentloaded")
    return page
