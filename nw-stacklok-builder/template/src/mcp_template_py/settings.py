from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
    )

    # Add your settings fields here
    debug: bool = Field(default=False, description="Enable debug logging if 'true'")
    mcp_host: str = Field(
        default="0.0.0.0",  # nosec B104 - intentional for container deployments
        description="Host for the MCP server to listen on",
    )
    mcp_port: int = Field(
        default=8100, description="Port for the MCP server to listen on"
    )
    server_url: str = Field(
        default="http://localhost:8100", description="Base URL of the server"
    )
    require_bearer_token: bool = Field(
        default=True,
        description="Return 401 for MCP requests without a Bearer token",
    )

    @field_validator("debug", "require_bearer_token", mode="before")
    @classmethod
    def parse_bool(cls, value: str | bool) -> bool:
        """Parse a string or bool value to a boolean."""
        if isinstance(value, bool):
            return value
        return value.lower() in ("true", "1", "yes", "on")
