"""RDP session manager backed by aardwolf, partitioned by agent bearer.

Each bearer (an opaque string sent by the calling agent, defaulting to
``"anonymous"``) owns its own ``AgentState`` containing an independent
session pool and active session pointer.  Agent A cannot enumerate or
operate on Agent B's sessions; two agents may freely open separate RDP
connections to the same target host.
"""

from __future__ import annotations

import asyncio
import io
import logging
import uuid
from dataclasses import dataclass, field
from typing import Optional
from urllib.parse import quote

from PIL import Image

log = logging.getLogger("rdp_mcp.manager")


# ---------------------------------------------------------------------------
# Scancode table — US layout, Set 1.  High byte 0xE0 means "extended" key
# (aardwolf wants is_extended=True for those).
# ---------------------------------------------------------------------------

_SCANCODES: dict[str, int] = {
    **{c: code for c, code in zip(
        "abcdefghijklmnopqrstuvwxyz",
        [0x1E, 0x30, 0x2E, 0x20, 0x12, 0x21, 0x22, 0x23, 0x17, 0x24,
         0x25, 0x26, 0x32, 0x31, 0x18, 0x19, 0x10, 0x13, 0x1F, 0x14,
         0x16, 0x2F, 0x11, 0x2D, 0x15, 0x2C],
    )},
    **{d: 0x02 + i for i, d in enumerate("1234567890")},
    "enter": 0x1C, "return": 0x1C,
    "esc": 0x01, "escape": 0x01,
    "backspace": 0x0E, "bs": 0x0E,
    "tab": 0x0F,
    "space": 0x39,
    "capslock": 0x3A,
    "lshift": 0x2A, "shift": 0x2A,
    "rshift": 0x36,
    "lctrl": 0x1D, "ctrl": 0x1D, "control": 0x1D,
    "rctrl": 0xE01D,
    "lalt": 0x38, "alt": 0x38,
    "ralt": 0xE038, "altgr": 0xE038,
    "lwin": 0xE05B, "win": 0xE05B, "super": 0xE05B,
    "rwin": 0xE05C,
    "menu": 0xE05D, "apps": 0xE05D,
    "f1": 0x3B, "f2": 0x3C, "f3": 0x3D, "f4": 0x3E, "f5": 0x3F,
    "f6": 0x40, "f7": 0x41, "f8": 0x42, "f9": 0x43, "f10": 0x44,
    "f11": 0x57, "f12": 0x58,
    "insert": 0xE052, "ins": 0xE052,
    "delete": 0xE053, "del": 0xE053,
    "printscreen": 0xE037, "print_screen": 0xE037, "prtsc": 0xE037,
    "home": 0xE047,
    "end": 0xE04F,
    "pageup": 0xE049, "pgup": 0xE049,
    "pagedown": 0xE051, "pgdn": 0xE051,
    "up": 0xE048, "down": 0xE050, "left": 0xE04B, "right": 0xE04D,
    ";": 0x27, "=": 0x0D, ",": 0x33, "-": 0x0C, ".": 0x34, "/": 0x35,
    "`": 0x29, "[": 0x1A, "\\": 0x2B, "]": 0x1B, "'": 0x28,
}


def _resolve_key(name: str) -> tuple[int, bool]:
    raw = _SCANCODES.get(name.lower())
    if raw is None:
        raise ValueError(f"Unknown key: {name!r}")
    if raw & 0xFF00 == 0xE000:
        return raw & 0xFF, True
    return raw, False


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

ANONYMOUS = "anonymous"


@dataclass
class ConnectionParams:
    host: str
    port: int
    username: str
    password: str
    domain: str = ""
    width: int = 1280
    height: int = 720


@dataclass
class RDPSession:
    sid: str
    name: str
    params: ConnectionParams
    conn: "object"  # aardwolf RDPConnection
    last_x: int = 0
    last_y: int = 0
    connected_at: float = 0.0


@dataclass
class AgentState:
    bearer: str
    sessions: dict[str, RDPSession] = field(default_factory=dict)
    active_id: Optional[str] = None


# ---------------------------------------------------------------------------
# Session manager — agent-partitioned
# ---------------------------------------------------------------------------

class SessionManager:
    def __init__(self) -> None:
        self.agents: dict[str, AgentState] = {}

    # -- agent partitioning -------------------------------------------------

    def _agent(self, bearer: Optional[str]) -> AgentState:
        key = bearer or ANONYMOUS
        st = self.agents.get(key)
        if st is None:
            st = AgentState(bearer=key)
            self.agents[key] = st
        return st

    def _active(self, bearer: Optional[str]) -> RDPSession:
        agent = self._agent(bearer)
        if agent.active_id is None or agent.active_id not in agent.sessions:
            raise KeyError(
                f"No active RDP session for agent {agent.bearer!r}. "
                "Call connect() first."
            )
        return agent.sessions[agent.active_id]

    def active_id_of(self, bearer: Optional[str]) -> Optional[str]:
        agent = self.agents.get(bearer or ANONYMOUS)
        return agent.active_id if agent else None

    # -- session lifecycle --------------------------------------------------

    async def connect(
        self,
        bearer: Optional[str],
        params: ConnectionParams,
        name: Optional[str] = None,
    ) -> str:
        from aardwolf.commons.factory import RDPConnectionFactory
        from aardwolf.commons.iosettings import RDPIOSettings
        from aardwolf.commons.queuedata.constants import VIDEO_FORMAT

        agent = self._agent(bearer)

        iosettings = RDPIOSettings()
        iosettings.video_width = params.width
        iosettings.video_height = params.height
        iosettings.video_bpp_min = 15
        iosettings.video_bpp_max = 32
        iosettings.video_out_format = VIDEO_FORMAT.PIL
        iosettings.clipboard_use_pyperclip = False

        user = params.username
        if params.domain:
            user = f"{params.domain}\\{params.username}"
        url = (
            f"rdp+ntlm-password://"
            f"{quote(user, safe='')}:{quote(params.password, safe='')}"
            f"@{params.host}:{params.port}"
        )
        log.info("[%s] Connecting RDP %s as %s", agent.bearer, params.host, user)

        factory = RDPConnectionFactory.from_url(url, iosettings)
        conn = factory.get_connection(iosettings)

        _, err = await conn.connect()
        if err is not None:
            raise RuntimeError(f"RDP connect failed: {err}")

        sid = uuid.uuid4().hex[:12]
        session = RDPSession(
            sid=sid,
            name=name or f"{params.host}:{params.port}",
            params=params,
            conn=conn,
            connected_at=asyncio.get_event_loop().time(),
        )
        agent.sessions[sid] = session
        agent.active_id = sid
        log.info("[%s] Connected session %s (%s) — now active",
                 agent.bearer, sid, session.name)
        return sid

    async def disconnect(self, bearer: Optional[str]) -> str:
        agent = self._agent(bearer)
        session = self._active(bearer)
        try:
            terminate = getattr(session.conn, "terminate", None)
            if terminate is None:
                terminate = getattr(session.conn, "send_disconnect", None)
            if terminate is not None:
                res = terminate()
                if asyncio.iscoroutine(res):
                    await res
        except Exception as exc:
            log.warning("[%s] terminate failed for %s: %s",
                        agent.bearer, session.sid, exc)
        agent.sessions.pop(session.sid, None)
        agent.active_id = next(iter(agent.sessions), None)
        return session.sid

    # -- queries ------------------------------------------------------------

    def list_sessions(self, bearer: Optional[str]) -> list[dict]:
        agent = self.agents.get(bearer or ANONYMOUS)
        if agent is None:
            return []
        return [
            {
                "session_id": s.sid,
                "name": s.name,
                "host": s.params.host,
                "port": s.params.port,
                "user": s.params.username,
                "size": [s.params.width, s.params.height],
                "active": s.sid == agent.active_id,
            }
            for s in agent.sessions.values()
        ]

    def switch(self, bearer: Optional[str], sid: str) -> None:
        agent = self._agent(bearer)
        if sid not in agent.sessions:
            raise KeyError(f"Unknown session for agent {agent.bearer!r}: {sid}")
        agent.active_id = sid

    # -- input/output (always operates on the agent's active session) -------

    async def snapshot(
        self,
        bearer: Optional[str],
        wait_seconds: float = 5.0,
    ) -> bytes:
        from aardwolf.commons.queuedata.constants import VIDEO_FORMAT
        session = self._active(bearer)
        deadline = asyncio.get_event_loop().time() + wait_seconds
        while not getattr(session.conn, "desktop_buffer_has_data", False):
            if asyncio.get_event_loop().time() > deadline:
                break
            await asyncio.sleep(0.1)
        img = session.conn.get_desktop_buffer(VIDEO_FORMAT.PIL)
        if img is None:
            img = Image.new("RGB", (session.params.width, session.params.height),
                            (0, 0, 0))
        if img.mode != "RGB":
            img = img.convert("RGB")
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        return buf.getvalue()

    async def move_mouse(self, bearer: Optional[str], x: int, y: int) -> None:
        from aardwolf.commons.queuedata.constants import MOUSEBUTTON
        session = self._active(bearer)
        session.last_x, session.last_y = int(x), int(y)
        await session.conn.send_mouse(
            MOUSEBUTTON.MOUSEBUTTON_HOVER, int(x), int(y), False,
        )

    async def click_mouse(
        self,
        bearer: Optional[str],
        button: str = "left",
        x: Optional[int] = None,
        y: Optional[int] = None,
        double: bool = False,
    ) -> None:
        from aardwolf.commons.queuedata.constants import MOUSEBUTTON
        session = self._active(bearer)
        btn_map = {
            "left":   MOUSEBUTTON.MOUSEBUTTON_LEFT,
            "right":  MOUSEBUTTON.MOUSEBUTTON_RIGHT,
            "middle": MOUSEBUTTON.MOUSEBUTTON_MIDDLE,
        }
        if button not in btn_map:
            raise ValueError(f"button must be one of {list(btn_map)}")
        btn = btn_map[button]
        if x is not None and y is not None:
            await self.move_mouse(bearer, x, y)
            await asyncio.sleep(0.03)
        cx = int(x) if x is not None else session.last_x
        cy = int(y) if y is not None else session.last_y
        for _ in range(2 if double else 1):
            await session.conn.send_mouse(btn, cx, cy, True)
            await asyncio.sleep(0.02)
            await session.conn.send_mouse(btn, cx, cy, False)
            if double:
                await asyncio.sleep(0.05)

    async def keyboard(
        self,
        bearer: Optional[str],
        text: Optional[str] = None,
        keys: Optional[list[str]] = None,
    ) -> None:
        session = self._active(bearer)

        if text:
            for ch in text:
                await session.conn.send_key_char(ch, True)
                await session.conn.send_key_char(ch, False)
                await asyncio.sleep(0.005)
            return

        if not keys:
            return

        resolved = [_resolve_key(k) for k in keys]
        for code, ext in resolved:
            await session.conn.send_key_scancode(code, True, ext)
            await asyncio.sleep(0.01)
        for code, ext in reversed(resolved):
            await session.conn.send_key_scancode(code, False, ext)
            await asyncio.sleep(0.01)


# Module-level singleton used by server.py
session_manager = SessionManager()
