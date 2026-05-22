"""Manager-level multi-agent isolation tests (no network).

Verifies that two bearers see completely independent session pools and
cannot cross-talk: list_sessions, switch, active_id_of, disconnect.
"""

from __future__ import annotations

import asyncio
import sys

from manager import (
    ANONYMOUS,
    AgentState,
    ConnectionParams,
    RDPSession,
    SessionManager,
)


def _fake_session(sid: str, host: str) -> RDPSession:
    """Build an RDPSession with no real aardwolf connection. Methods that
    require `conn` (snapshot/move_mouse/click/keyboard/disconnect) won't
    be exercised in these tests — only metadata-level isolation is."""
    return RDPSession(
        sid=sid, name=f"fake-{sid}",
        params=ConnectionParams(host=host, port=3389,
                                username="u", password="p"),
        conn=object(),
    )


def test_default_bearer_is_anonymous() -> None:
    sm = SessionManager()
    # No agents yet → list returns empty for any bearer
    assert sm.list_sessions(None) == []
    assert sm.list_sessions("") == []
    assert sm.list_sessions("anyone") == []
    # ANONYMOUS constant is the fallback key for None/""
    assert sm._agent(None).bearer == ANONYMOUS
    assert sm._agent("").bearer == ANONYMOUS


def test_two_agents_isolated_listing() -> None:
    sm = SessionManager()
    alice = sm._agent("alice")
    bob = sm._agent("bob")
    alice.sessions["s1"] = _fake_session("s1", "10.0.0.1")
    alice.active_id = "s1"
    bob.sessions["s2"] = _fake_session("s2", "10.0.0.2")
    bob.active_id = "s2"

    a_list = sm.list_sessions("alice")
    b_list = sm.list_sessions("bob")
    assert len(a_list) == 1 and a_list[0]["session_id"] == "s1"
    assert len(b_list) == 1 and b_list[0]["session_id"] == "s2"
    # No cross-contamination
    assert sm.list_sessions("alice")[0]["host"] == "10.0.0.1"
    assert sm.list_sessions("bob")[0]["host"] == "10.0.0.2"
    # Anonymous still empty
    assert sm.list_sessions(None) == []


def test_switch_cannot_cross_agents() -> None:
    sm = SessionManager()
    alice = sm._agent("alice")
    alice.sessions["s1"] = _fake_session("s1", "10.0.0.1")
    alice.active_id = "s1"

    # Bob tries to switch to Alice's session → KeyError
    try:
        sm.switch("bob", "s1")
    except KeyError as e:
        assert "bob" in repr(e)
    else:
        raise AssertionError("expected KeyError when bob targets alice's sid")

    # Alice can switch to her own sid
    alice.sessions["s2"] = _fake_session("s2", "10.0.0.1")
    sm.switch("alice", "s2")
    assert sm.active_id_of("alice") == "s2"
    assert sm.active_id_of("bob") is None


def test_active_id_isolation() -> None:
    sm = SessionManager()
    sm._agent("alice").sessions["s1"] = _fake_session("s1", "1")
    sm._agent("alice").active_id = "s1"
    assert sm.active_id_of("alice") == "s1"
    assert sm.active_id_of("bob") is None
    assert sm.active_id_of(None) is None
    assert sm.active_id_of("anonymous") is None


def test_same_host_two_agents_distinct_sessions() -> None:
    """Both alice and bob may track a session pointing at the same host
    — they're independent RDP connections at the aardwolf layer, and at
    the MCP layer they're independent records."""
    sm = SessionManager()
    sm._agent("alice").sessions["s1"] = _fake_session("s1", "192.168.100.185")
    sm._agent("alice").active_id = "s1"
    sm._agent("bob").sessions["s2"] = _fake_session("s2", "192.168.100.185")
    sm._agent("bob").active_id = "s2"

    assert sm.list_sessions("alice")[0]["session_id"] == "s1"
    assert sm.list_sessions("bob")[0]["session_id"] == "s2"
    assert sm.list_sessions("alice")[0]["session_id"] != \
           sm.list_sessions("bob")[0]["session_id"]


def test_disconnect_only_affects_own_pool() -> None:
    """Manager.disconnect() removes the active session from the calling
    agent's pool without touching another agent's pool. We monkey-patch
    aardwolf.terminate to avoid network calls."""
    sm = SessionManager()

    class FakeConn:
        terminated = False
        async def terminate(self):
            self.terminated = True

    alice_conn = FakeConn()
    bob_conn = FakeConn()
    sm._agent("alice").sessions["s1"] = RDPSession(
        sid="s1", name="a", params=ConnectionParams("h", 3389, "u", "p"),
        conn=alice_conn,
    )
    sm._agent("alice").active_id = "s1"
    sm._agent("bob").sessions["s2"] = RDPSession(
        sid="s2", name="b", params=ConnectionParams("h", 3389, "u", "p"),
        conn=bob_conn,
    )
    sm._agent("bob").active_id = "s2"

    async def run():
        closed = await sm.disconnect("alice")
        assert closed == "s1"
        # Alice's pool is empty
        assert sm.list_sessions("alice") == []
        assert sm.active_id_of("alice") is None
        # Bob's pool is untouched
        assert len(sm.list_sessions("bob")) == 1
        assert sm.active_id_of("bob") == "s2"
        # Alice's conn terminated, Bob's did not
        assert alice_conn.terminated is True
        assert bob_conn.terminated is False

    asyncio.run(run())


def test_bearer_middleware_extraction() -> None:
    """Verify BearerMiddleware writes the right token into current_bearer
    for several Authorization header shapes."""
    from server import BearerMiddleware, current_bearer

    seen: list[str] = []

    async def downstream(scope, receive, send):
        seen.append(current_bearer.get())

    mw = BearerMiddleware(downstream)

    async def call(headers: list[tuple[bytes, bytes]]):
        scope = {"type": "http", "headers": headers, "method": "POST",
                 "path": "/mcp"}
        async def recv(): return {"type": "http.disconnect"}
        async def send(_): pass
        await mw(scope, recv, send)

    async def run():
        # 1) Valid bearer
        await call([(b"authorization", b"Bearer alice-secret")])
        # 2) Lower-case scheme
        await call([(b"authorization", b"bearer bob-secret")])
        # 3) Extra spaces
        await call([(b"authorization", b"Bearer   carol-secret  ")])
        # 4) Missing header
        await call([])
        # 5) Empty bearer
        await call([(b"authorization", b"Bearer ")])
        # 6) Wrong scheme
        await call([(b"authorization", b"Basic dXNlcjpwYXNz")])
        # 7) Lifespan event must pass through untouched
        async def lifespan_downstream(s, r, sd):
            seen.append("LIFESPAN_" + current_bearer.get())
        lmw = BearerMiddleware(lifespan_downstream)
        await lmw({"type": "lifespan"}, lambda: None, lambda x: None)

    asyncio.run(run())

    assert seen == [
        "alice-secret",
        "bob-secret",
        "carol-secret",
        "anonymous",
        "anonymous",
        "anonymous",
        # Lifespan path: bearer must be the contextvar's default since
        # the middleware skipped the rewrite for non-HTTP scopes.
        "LIFESPAN_anonymous",
    ], f"got {seen!r}"


def test_bearer_does_not_leak_between_calls() -> None:
    """Two sequential requests with different bearers must not bleed."""
    from server import BearerMiddleware, current_bearer

    captured: list[str] = []

    async def downstream(scope, receive, send):
        captured.append(current_bearer.get())

    mw = BearerMiddleware(downstream)

    async def run():
        for token in ["A", "B", "A", "C"]:
            await mw(
                {"type": "http",
                 "headers": [(b"authorization", f"Bearer {token}".encode())],
                 "method": "POST", "path": "/mcp"},
                lambda: None, lambda x: None,
            )
        # After all requests the contextvar must return to default
        assert current_bearer.get() == "anonymous"

    asyncio.run(run())
    assert captured == ["A", "B", "A", "C"], f"got {captured!r}"


def main() -> int:
    tests = [
        test_default_bearer_is_anonymous,
        test_two_agents_isolated_listing,
        test_switch_cannot_cross_agents,
        test_active_id_isolation,
        test_same_host_two_agents_distinct_sessions,
        test_disconnect_only_affects_own_pool,
        test_bearer_middleware_extraction,
        test_bearer_does_not_leak_between_calls,
    ]
    failed = 0
    for t in tests:
        try:
            t()
        except Exception as e:
            failed += 1
            print(f"[FAIL] {t.__name__}: {type(e).__name__}: {e}")
        else:
            print(f"[ ok ] {t.__name__}")
    if failed:
        print(f"\n{failed} of {len(tests)} test(s) failed.")
        return 1
    print(f"\nAll {len(tests)} tests passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
