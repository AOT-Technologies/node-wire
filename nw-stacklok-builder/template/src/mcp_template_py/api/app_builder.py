from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any, cast

import structlog
from starlette.applications import Starlette
from starlette.middleware import Middleware
from starlette.routing import Mount

from mcp_template_py.api.mcp_builder import MCPBuilder
from mcp_template_py.auth import TokenPassthroughMiddleware
from mcp_template_py.settings import Settings


class AppBuilder:
    logger = structlog.get_logger()

    @staticmethod
    def build_app(settings: Settings | None = None) -> Starlette:
        settings = settings or Settings()

        mcp = MCPBuilder.build_mcp(settings)
        mcp_http_app = mcp.streamable_http_app()

        # Lifespan to properly initialize the MCP session manager.
        # When mounting streamable_http_app() as a sub-app, Starlette doesn't
        # trigger its lifespan, so we must run the session manager explicitly.
        # See: https://github.com/modelcontextprotocol/python-sdk/issues/1467
        @asynccontextmanager
        async def lifespan(_app: Starlette) -> AsyncIterator[None]:
            async with mcp.session_manager.run():
                AppBuilder.logger.info("MCP session manager started")
                yield
            AppBuilder.logger.info("MCP session manager stopped")

        middleware = [
            Middleware(
                cast(Any, TokenPassthroughMiddleware),
                require_bearer_token=settings.require_bearer_token,
            ),
        ]

        routes = [Mount("/", app=mcp_http_app, middleware=middleware)]

        app = Starlette(
            routes=routes,
            lifespan=lifespan,
        )

        return app
