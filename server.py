"""rdp-mcp — MCP server exposing RDP sessions as tools.

Per-agent session isolation: a calling agent identifies itself with an
``Authorization: Bearer <token>`` header (HTTP transport). Sessions live
in a pool scoped to that bearer; one agent never sees another agent's
sessions. Missing/empty bearer is accepted and mapped to the
``anonymous`` agent.

Run as stdio (default — bearer is always ``anonymous``):
    python server.py

Run as HTTP (multi-agent, bearer-aware):
    python server.py --http              # 127.0.0.1:8765
    python server.py --http 0.0.0.0:9000
"""

from __future__ import annotations

import argparse
import contextvars
import logging
import sys
from pathlib import Path
from typing import Optional

from mcp.server.fastmcp import FastMCP, Image

from manager import ConnectionParams, session_manager

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(name)s %(levelname)s: %(message)s",
    stream=sys.stderr,
)

# Per-request agent identity. Set by BearerMiddleware in HTTP mode; in
# stdio mode it stays at the default and every call maps to one agent.
current_bearer: contextvars.ContextVar[str] = contextvars.ContextVar(
    "rdp_mcp.current_bearer", default="anonymous"
)


def _bearer() -> str:
    return current_bearer.get()


# ---------------------------------------------------------------------------
# Tools
# ---------------------------------------------------------------------------

mcp = FastMCP("rdp-mcp")


@mcp.tool()
async def connect(
    host: str,
    username: str,
    password: str,
    port: int = 3389,
    domain: str = "",
    name: Optional[str] = None,
    width: int = 1280,
    height: int = 720,
) -> dict:
    """Open an RDP session inside the caller's agent pool, start streaming
    its screen, and make it the active session for that agent. Sessions
    are never shared between agents (identified by the bearer token).
    """
    bearer = _bearer()
    params = ConnectionParams(
        host=host, port=port,
        username=username, password=password, domain=domain,
        width=width, height=height,
    )
    sid = await session_manager.connect(bearer, params, name=name)
    return {
        "agent": bearer,
        "session_id": sid,
        "host": host,
        "port": port,
        "user": (f"{domain}\\{username}" if domain else username),
        "size": [width, height],
        "active": True,
    }


@mcp.tool()
async def list_connections() -> dict:
    """List every open RDP session belonging to the calling agent."""
    bearer = _bearer()
    return {
        "agent": bearer,
        "sessions": session_manager.list_sessions(bearer),
    }


@mcp.tool()
async def switch(session_id: str) -> dict:
    """Change which of *this agent's* sessions is active. Subsequent
    snapshot / move_mouse / click_mouse / keyboard / disconnect calls
    target this one."""
    bearer = _bearer()
    session_manager.switch(bearer, session_id)
    return {"agent": bearer, "active_session_id": session_id}


@mcp.tool()
async def snapshot(path: Optional[str] = None) -> Image:
    """Return a PNG screenshot of the calling agent's active session.

    If `path` is provided, also save the PNG bytes to that local path.
    """
    png = await session_manager.snapshot(_bearer())
    if path:
        output_path = Path(path).expanduser()
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_bytes(png)
    return Image(data=png, format="png")


@mcp.tool()
async def move_mouse(x: int, y: int) -> dict:
    """Move the remote cursor of the agent's active session to (x, y)."""
    await session_manager.move_mouse(_bearer(), x, y)
    return {"ok": True, "x": x, "y": y}


@mcp.tool()
async def click_mouse(
    button: str = "left",
    x: Optional[int] = None,
    y: Optional[int] = None,
    double: bool = False,
) -> dict:
    """Click a mouse button on the agent's active session.

    `button` ∈ {"left", "right", "middle"}. If `x`/`y` supplied the cursor
    moves there first; otherwise the click fires at the cursor's last
    position. Set `double=true` for a double-click.
    """
    await session_manager.click_mouse(
        _bearer(), button=button, x=x, y=y, double=double,
    )
    return {"ok": True, "button": button, "double": double, "x": x, "y": y}


@mcp.tool()
async def keyboard(
    text: Optional[str] = None,
    keys: Optional[list[str]] = None,
) -> dict:
    """Send keystrokes to the agent's active session.

    Provide one of:
      - `text`: a unicode string sent character-by-character.
      - `keys`: a chord like ["ctrl", "shift", "esc"] — pressed in order,
                released in reverse.
    """
    if not text and not keys:
        raise ValueError("Provide `text` or `keys`")
    await session_manager.keyboard(_bearer(), text=text, keys=keys)
    return {"ok": True}


@mcp.tool()
async def disconnect() -> dict:
    """Close the calling agent's active session. If the same agent owns
    other sessions one becomes the new active automatically."""
    bearer = _bearer()
    closed = await session_manager.disconnect(bearer)
    return {
        "disconnected": closed,
        "new_active": session_manager.active_id_of(bearer),
    }


# ---------------------------------------------------------------------------
# ASGI middleware for HTTP transport — extract Authorization: Bearer
# ---------------------------------------------------------------------------

class BearerMiddleware:
    """Stash the request's bearer token into the ``current_bearer``
    contextvar so each tool call can read it without inspecting the
    transport. Anything that's not a non-empty bearer becomes
    ``"anonymous"``."""

    def __init__(self, app) -> None:
        self.app = app

    async def __call__(self, scope, receive, send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        bearer = "anonymous"
        for raw_k, raw_v in scope.get("headers", []):
            if raw_k.lower() == b"authorization":
                try:
                    val = raw_v.decode("latin-1", errors="ignore")
                except Exception:
                    val = ""
                if val[:7].lower() == "bearer ":
                    token = val[7:].strip()
                    if token:
                        bearer = token
                break

        cv_token = current_bearer.set(bearer)
        try:
            await self.app(scope, receive, send)
        finally:
            current_bearer.reset(cv_token)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def _parse_host_port(raw: str, default_port: int = 8765) -> tuple[str, int]:
    host, _, port_s = raw.partition(":")
    host = host or "127.0.0.1"
    port = int(port_s) if port_s else default_port
    return host, port


def main() -> None:
    parser = argparse.ArgumentParser(description="rdp-mcp server")
    parser.add_argument(
        "--http",
        nargs="?",
        const="127.0.0.1:8765",
        default=None,
        metavar="HOST:PORT",
        help="run streamable-HTTP server (default: stdio). Without an "
             "argument binds to 127.0.0.1:8765.",
    )
    args = parser.parse_args()

    if args.http:
        import uvicorn

        host, port = _parse_host_port(args.http)
        app = mcp.streamable_http_app()
        app = BearerMiddleware(app)
        logging.info("rdp-mcp HTTP transport listening on %s:%d", host, port)
        uvicorn.run(app, host=host, port=port, log_level="info",
                    access_log=False)
    else:
        logging.info("rdp-mcp stdio transport — all calls map to 'anonymous'")
        mcp.run()


if __name__ == "__main__":
    main()
