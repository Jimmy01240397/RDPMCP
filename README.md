# rdp-mcp

MCP server that exposes one or more live RDP sessions as tools:
`connect`, `list_connections`, `switch`, `snapshot`, `move_mouse`,
`click_mouse`, `keyboard`, `disconnect`.

Backed by [aardwolf](https://github.com/skelsec/aardwolf) — pure-Python,
async RDP client supporting NLA (CredSSP/NTLM), Set-1 scancode input,
unicode keyboard, and PIL-formatted video output.

## Install

```powershell
cd z:\CTF\pwn2own\berlin2026\rds\rdp_mcp
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

## Run (stdio)

```powershell
.\.venv\Scripts\python.exe server.py
```

## Register with Claude Code

Add to `~/.claude/settings.json` (adjust path):

```json
{
  "mcpServers": {
    "rdp": {
      "command": "z:\\CTF\\pwn2own\\berlin2026\\rds\\rdp_mcp\\.venv\\Scripts\\python.exe",
      "args": ["z:\\CTF\\pwn2own\\berlin2026\\rds\\rdp_mcp\\server.py"]
    }
  }
}
```

Or in Claude Desktop's `claude_desktop_config.json` with the same shape.

## Tools

| Tool | Purpose | Args |
|------|---------|------|
| `connect` | Open a new RDP session (becomes active) | `host`, `username`, `password`, `port=3389`, `domain=""`, `name=None`, `width=1280`, `height=720` |
| `list_connections` | Enumerate open sessions | — |
| `switch` | Pick which session is the active target | `session_id` |
| `snapshot` | Return PNG screenshot of active session | — |
| `move_mouse` | Move remote cursor on active session | `x`, `y` |
| `click_mouse` | Click / double-click on active session | `button="left"\|"right"\|"middle"`, `x`, `y`, `double=false` |
| `keyboard` | Type text or send a chord on active session | `text=None`, `keys=None` (e.g. `["ctrl","c"]`) |
| `disconnect` | Close the active session | — |

Every input/output tool implicitly targets the **active** session.
`connect` makes the new session active; use `switch` to retarget.
After `disconnect`, the next remaining session (if any) becomes active.

## Quick test against 192.168.100.185 (testrdp / P@ssw0rd123)

```python
await connect(host="192.168.100.185",
              username="testrdp",
              password="P@ssw0rd123",
              name="vboxlab")           # active = vboxlab
await snapshot()                         # PNG of the desktop
await move_mouse(640, 360)
await click_mouse("left", 640, 360)
await keyboard(keys=["lwin", "r"])       # Win+R
await keyboard(text="notepad.exe")
await keyboard(keys=["enter"])
await disconnect()
```

Two boxes in parallel:

```python
a = (await connect(host="192.168.100.185", username="testrdp",
                   password="P@ssw0rd123", name="A"))["session_id"]
b = (await connect(host="192.168.100.174", username="testrdp",
                   password="P@ssw0rd123!", name="B"))["session_id"]
# B is now active (last connect wins).
await snapshot()                # -> B
await switch(a); await snapshot()  # -> A
await switch(b); await disconnect()  # closes B, A becomes active
```

## Notes / caveats

- The video buffer is composited from server-pushed bitmap updates.
  Right after `connect()` only the areas the server has redrawn are
  populated — wiggle the mouse or click once to provoke a full repaint
  if the first `snapshot` looks empty. `snapshot` already waits up to
  5 s for the first bitmap update to arrive.
- Scancode chords (`keys`) use the US Set-1 layout; non-US characters
  should go through `text=` which uses RDP unicode keyboard events.
- aardwolf uses NLA (`rdp+ntlm-password://`) by default; if a target
  insists on RDSTLS or plain TLS, edit the URL scheme in
  `manager.SessionManager.connect`.
- Only the active session has an implicit default — all tools accept an
  explicit `session_id` so an LLM can drive several boxes at once.

## Files

- `server.py` — FastMCP entry point, defines the 8 tools.
- `manager.py` — `SessionManager`: aardwolf wiring, frame buffer, input.
- `pyproject.toml` / `requirements.txt` — deps.
