"""rdp-mcp — MCP server exposing RDP sessions as tools.

Every input/output tool acts on the *active* session. Use `connect`
(creates and activates) or `switch(session_id)` to change which one.

Run as a stdio MCP server:
    python server.py
"""

from __future__ import annotations

import logging
import sys
from typing import Optional

from mcp.server.fastmcp import FastMCP, Image

from manager import ConnectionParams, session_manager

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(name)s %(levelname)s: %(message)s",
    stream=sys.stderr,
)

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
    """Open an RDP session, start streaming its screen, and make it the
    active session. All later snapshot / move_mouse / click_mouse /
    keyboard / disconnect calls operate on this session until you call
    `switch` to target a different one.
    """
    params = ConnectionParams(
        host=host,
        port=port,
        username=username,
        password=password,
        domain=domain,
        width=width,
        height=height,
    )
    sid = await session_manager.connect(params, name=name)
    return {
        "session_id": sid,
        "host": host,
        "port": port,
        "user": (f"{domain}\\{username}" if domain else username),
        "size": [width, height],
        "active": True,
    }


@mcp.tool()
async def list_connections() -> list[dict]:
    """List every open RDP session. The active one has `active: true`."""
    return session_manager.list_sessions()


@mcp.tool()
async def switch(session_id: str) -> dict:
    """Change which session is active. Subsequent snapshot / move_mouse /
    click_mouse / keyboard / disconnect calls will target this one."""
    session_manager.switch(session_id)
    return {"active_session_id": session_id}


@mcp.tool()
async def snapshot() -> Image:
    """Return a PNG screenshot of the active RDP session."""
    png = await session_manager.snapshot()
    return Image(data=png, format="png")


@mcp.tool()
async def move_mouse(x: int, y: int) -> dict:
    """Move the remote cursor of the active session to absolute pixel (x, y)."""
    await session_manager.move_mouse(x, y)
    return {"ok": True, "x": x, "y": y}


@mcp.tool()
async def click_mouse(
    button: str = "left",
    x: Optional[int] = None,
    y: Optional[int] = None,
    double: bool = False,
) -> dict:
    """Click a mouse button on the active session.

    `button` ∈ {"left", "right", "middle"}. If `x`/`y` supplied the cursor
    moves there first; otherwise the click fires at the cursor's last spot.
    Set `double=true` for a double-click.
    """
    await session_manager.click_mouse(button=button, x=x, y=y, double=double)
    return {"ok": True, "button": button, "double": double, "x": x, "y": y}


@mcp.tool()
async def keyboard(
    text: Optional[str] = None,
    keys: Optional[list[str]] = None,
) -> dict:
    """Send keystrokes to the active session.

    Provide one of:
      - `text`: a unicode string sent character-by-character (best for typing).
      - `keys`: a chord like ["ctrl", "shift", "esc"] — pressed in order,
                released in reverse. Friendly names: ctrl/alt/shift/win,
                f1..f12, enter, esc, tab, space, backspace, delete, up, down,
                left, right, home, end, pageup, pagedown, letters, digits, ...
    """
    if not text and not keys:
        raise ValueError("Provide `text` or `keys`")
    await session_manager.keyboard(text=text, keys=keys)
    return {"ok": True}


@mcp.tool()
async def disconnect() -> dict:
    """Close the active RDP session. If other sessions exist one of them
    automatically becomes the new active."""
    closed = await session_manager.disconnect()
    return {"disconnected": closed, "new_active": session_manager.active_id}


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()
