"""Access-log middleware: one log line per tool call with the client identity.

The identity is the ``client_id`` of the verified bearer token (the token ID
when using a tokens file), or ``local`` when no authentication is in play
(stdio transport, or HTTP without a configured verifier).
"""

import logging
import time

from fastmcp.server.dependencies import get_access_token
from fastmcp.server.middleware import Middleware

logger = logging.getLogger(__name__)


def _current_client_id() -> str:
    try:
        token = get_access_token()
    except Exception:
        return "local"
    return token.client_id if token else "local"


class AccessLogMiddleware(Middleware):
    """Log every tool call as ``client=… tool=… outcome=… duration_ms=…``."""

    async def on_call_tool(self, context, call_next):
        client = _current_client_id()
        tool = getattr(context.message, "name", "?")
        start = time.monotonic()
        try:
            result = await call_next(context)
        except Exception:
            duration_ms = int((time.monotonic() - start) * 1000)
            logger.info(
                "client=%s tool=%s outcome=error duration_ms=%d",
                client,
                tool,
                duration_ms,
            )
            raise
        duration_ms = int((time.monotonic() - start) * 1000)
        logger.info(
            "client=%s tool=%s outcome=ok duration_ms=%d", client, tool, duration_ms
        )
        return result
