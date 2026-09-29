import contextvars

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse

_current_bearer_token: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "_current_bearer_token", default=None
)


def get_bearer_token() -> str | None:
    """Get the Bearer token from the current request context.

    Call this from MCP tools to access the token sent by the client.
    Returns None if no Bearer token was provided.
    """
    return _current_bearer_token.get()


class TokenPassthroughMiddleware(BaseHTTPMiddleware):
    """Extracts Bearer tokens from request headers and makes them available to MCP tools."""

    def __init__(self, app, *, require_bearer_token: bool = True):
        super().__init__(app)
        self.require_bearer_token = require_bearer_token

    async def dispatch(self, request: Request, call_next):
        token = None
        auth_header = request.headers.get("Authorization", "")
        if auth_header.startswith("Bearer ") and len(auth_header) > 7:
            token = auth_header[7:]

        if self.require_bearer_token and token is None:
            return JSONResponse({"detail": "Bearer token required"}, status_code=401)

        ctx_token = _current_bearer_token.set(token)
        try:
            return await call_next(request)
        finally:
            _current_bearer_token.reset(ctx_token)
