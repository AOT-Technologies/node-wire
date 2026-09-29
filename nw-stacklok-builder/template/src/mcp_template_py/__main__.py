"""Entry point for the MCP server."""

import structlog

import uvicorn
from mcp_template_py.api.app_builder import AppBuilder
from mcp_template_py.configure_logging import configure_logging
from mcp_template_py.settings import Settings


if __name__ == "__main__":
    # Configure logging
    settings = Settings()
    log_level = "DEBUG" if settings.debug else "INFO"
    configure_logging(log_level=log_level)

    logger = structlog.get_logger()
    # Build and run the MCP server
    app = AppBuilder.build_app(settings)
    logger.info("Starting MCP server", host=settings.mcp_host, port=settings.mcp_port)
    uvicorn.run(
        app,
        host=settings.mcp_host,
        port=settings.mcp_port,
    )
