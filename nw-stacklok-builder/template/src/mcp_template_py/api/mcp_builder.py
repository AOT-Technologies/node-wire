import structlog
from mcp.server.fastmcp import FastMCP

from mcp_template_py.api.tools import Tools
from mcp_template_py.settings import Settings


class MCPBuilder:
    logger: structlog.BoundLogger = structlog.get_logger()

    @staticmethod
    def build_mcp(
        settings: Settings | None = None,
    ) -> FastMCP:
        """Build and configure the MCP server.

        Creates a FastMCP instance with tools.

        Args:
            settings: Application settings

        Returns:
            Configured FastMCP server instance
        """
        settings = settings or Settings()

        mcp = FastMCP("mcp-template-py", host=settings.mcp_host, port=settings.mcp_port)

        # Register tools directly - preserves original docstrings
        tools = Tools()
        mcp.add_tool(tools.hello)

        return mcp
