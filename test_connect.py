"""Smoke test for rdp_mcp without the MCP layer.

Run after `pip install -r requirements.txt` while .185 is up:

    python test_connect.py

Writes `smoketest_1.png` (initial desktop) and `smoketest_2.png`
(after centering the mouse). Confirms aardwolf wiring + screen buffer
+ mouse path before you wire the server into Claude.
"""

from __future__ import annotations

import argparse
import asyncio

from manager import ConnectionParams, session_manager


async def run(host: str, user: str, pw: str, port: int, w: int, h: int) -> None:
    sid = await session_manager.connect(
        ConnectionParams(host=host, port=port, username=user,
                         password=pw, width=w, height=h),
        name="smoke",
    )
    print(f"[+] connected {sid} ({host})")

    # Wiggle the cursor to provoke an initial paint, then snapshot once the
    # buffer reports it has real data.
    await session_manager.move_mouse(w // 2, h // 2)
    png = await session_manager.snapshot(wait_seconds=8.0)
    open("smoketest_1.png", "wb").write(png)
    print(f"[+] wrote smoketest_1.png ({len(png)} bytes)")

    # Click the Start button area to provoke more updates, then snapshot.
    await session_manager.click_mouse("left", 20, h - 20)
    await asyncio.sleep(1.0)
    png = await session_manager.snapshot()
    open("smoketest_2.png", "wb").write(png)
    print(f"[+] wrote smoketest_2.png ({len(png)} bytes)")

    # Type something into focused control (likely search box of start menu)
    await session_manager.keyboard(text="hello")
    await asyncio.sleep(0.6)
    png = await session_manager.snapshot()
    open("smoketest_3.png", "wb").write(png)
    print(f"[+] wrote smoketest_3.png ({len(png)} bytes)")

    # Close start menu
    await session_manager.keyboard(keys=["esc"])
    await asyncio.sleep(0.3)

    await session_manager.disconnect()
    print("[+] disconnected")


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--host", default="192.168.100.185")
    p.add_argument("--user", default="testrdp")
    p.add_argument("--password", default="P@ssw0rd123")
    p.add_argument("--port", type=int, default=3389)
    p.add_argument("--width", type=int, default=1280)
    p.add_argument("--height", type=int, default=720)
    a = p.parse_args()
    asyncio.run(run(a.host, a.user, a.password, a.port, a.width, a.height))


if __name__ == "__main__":
    main()
