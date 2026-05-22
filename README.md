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

## Run

Two transports:

```powershell
.\.venv\Scripts\python.exe server.py                  # stdio (default)
.\.venv\Scripts\python.exe server.py --http           # 127.0.0.1:8765
.\.venv\Scripts\python.exe server.py --http 0.0.0.0:9000
```

## Per-agent isolation

The HTTP transport partitions every session pool by the caller's
`Authorization: Bearer <token>` header. Two different agents never see
each other's sessions, even when both connect to the same target host.
Missing/empty bearer is accepted and mapped to the literal agent name
`anonymous` (every header-less request shares one pool).

| Bearer header                       | Agent identity     |
|-------------------------------------|--------------------|
| `Authorization: Bearer alice-key`   | `alice-key`        |
| `Authorization: Bearer bob-key`     | `bob-key`          |
| (none) / `Bearer ` (empty)          | `anonymous`        |
| `Basic xxx` (other scheme)          | `anonymous`        |

In **stdio** mode there is no per-request header, so every call maps to
the single `anonymous` agent — useful for single-user dev work.

## Register with Claude Code

**stdio** (single agent, simplest):
```powershell
claude mcp add rdp -- "z:\CTF\pwn2own\berlin2026\rds\rdp_mcp\.venv\Scripts\python.exe" `
                     "z:\CTF\pwn2own\berlin2026\rds\rdp_mcp\server.py"
```

**HTTP with per-agent bearer** (multiple Claude instances / sub-agents):

1. Start the server once:
   ```powershell
   .\.venv\Scripts\python.exe server.py --http
   ```
2. Each agent registers with its own bearer:
   ```powershell
   claude mcp add --transport http rdp http://127.0.0.1:8765/mcp `
       --header "Authorization: Bearer alice-key"
   ```
   or by hand in `~/.claude/settings.json`:
   ```json
   {
     "mcpServers": {
       "rdp": {
         "type": "http",
         "url": "http://127.0.0.1:8765/mcp",
         "headers": { "Authorization": "Bearer alice-key" }
       }
     }
   }
   ```
   A second agent uses a different token (`bob-key`, etc.); the server
   keeps their session pools entirely separate.

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

- `server.py` — FastMCP entry point + BearerMiddleware + transport CLI.
- `manager.py` — `SessionManager` partitioned by bearer (`AgentState`
  per agent); aardwolf wiring, frame buffer, mouse / keyboard.
- `test_connect.py` — single-agent end-to-end smoke (needs `.185`).
- `test_multi_agent.py` — 8 offline unit tests covering agent isolation
  and the BearerMiddleware extraction (no network).
- `pyproject.toml` / `requirements.txt` — deps.
