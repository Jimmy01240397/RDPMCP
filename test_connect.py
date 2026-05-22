"""Smoke test for rdp_mcp without the MCP layer.

Run after `pip install -r requirements.txt` while .185 is up:

    python test_connect.py

Writes `smoketest_1.png` / `_2.png` / `_3.png`. Confirms aardwolf
wiring + screen buffer + mouse/keyboard path. Drives one bearer
("smoke") to exercise the per-agent code path.
"""

from __future__ import annotations

import argparse
import asyncio

from manager import ConnectionParams, session_manager

BEARER = "smoke"  # exercise the per-agent partitioned manager API


async def run(host: str, user: str, pw: str, port: int, w: int, h: int) -> None:
    sid = await session_manager.connect(
        BEARER,
        ConnectionParams(host=host, port=port, username=user,
                         password=pw, width=w, height=h),
        name="smoke",
    )
    print(f"[+] connected {sid} ({host}) as agent={BEARER}")

    await session_manager.move_mouse(BEARER, w // 2, h // 2)
    png = await session_manager.snapshot(BEARER, wait_seconds=8.0)
    open("smoketest_1.png", "wb").write(png)
    print(f"[+] wrote smoketest_1.png ({len(png)} bytes)")

    await session_manager.click_mouse(BEARER, "left", 20, h - 20)
    await asyncio.sleep(1.0)
    png = await session_manager.snapshot(BEARER)
    open("smoketest_2.png", "wb").write(png)
    print(f"[+] wrote smoketest_2.png ({len(png)} bytes)")

    await session_manager.keyboard(BEARER, text="hello")
    await asyncio.sleep(0.6)
    png = await session_manager.snapshot(BEARER)
    open("smoketest_3.png", "wb").write(png)
    print(f"[+] wrote smoketest_3.png ({len(png)} bytes)")

    await session_manager.keyboard(BEARER, keys=["esc"])
    await asyncio.sleep(0.3)

    await session_manager.disconnect(BEARER)
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
