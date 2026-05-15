"""Tests for `_run_on_mcp_loop` orphaning coroutines on timeout.

Production wedge observed 2026-05-15 against eureka-mcp: when Coolify
replaces the MCP server container mid-session, hermes briefly tries to
call the old (now-dead) session, the SDK ``call_tool`` stalls instead of
failing fast (raw connection errors aren't delivered to the read stream
by ``streamable_http.post_writer``), and ``_run_on_mcp_loop`` hits its
``tool_timeout`` and raises ``TimeoutError`` to the caller.

But the deadline branch (``tools/mcp_tool.py::_run_on_mcp_loop``)
*never calls* ``future.cancel()`` — only the user-interrupt branch does.
So the underlying coroutine keeps running on the background MCP loop
forever.  Because every ``_call()`` runs inside ``async with
server._rpc_lock`` (and the same lock is also used by
``_discover_tools`` during reconnect), the per-server lock leaks.  The
reconnect's ``list_tools`` POST never goes out (``_discover_tools``
blocks on the leaked lock), and every subsequent tool call wedges too.
Only a hermes container restart — which gives a fresh ``_rpc_lock``
object — escapes it.

Two tests: the direct unit-level bug (no cancel on timeout) and the
production-shaped consequence (the leaked ``_rpc_lock``).
"""
import asyncio
import threading

import pytest


def test_run_on_mcp_loop_cancels_coroutine_on_timeout():
    """When the deadline fires, the coroutine MUST be cancelled.

    Without cancellation it becomes a permanent zombie task on the MCP
    loop, holding any locks / resources acquired before the hang.  This
    is the root of the production wedge — fixing this fixes the wedge.
    """
    from tools import mcp_tool

    mcp_tool._ensure_mcp_loop()

    cancelled = threading.Event()

    async def _hangs_forever():
        try:
            await asyncio.sleep(60)
        except asyncio.CancelledError:
            cancelled.set()
            raise

    with pytest.raises(TimeoutError):
        mcp_tool._run_on_mcp_loop(_hangs_forever(), timeout=0.3)

    # Cancellation is delivered via call_soon_threadsafe; give it a
    # generous window to land on the loop thread.
    assert cancelled.wait(timeout=2.0), (
        "_run_on_mcp_loop raised TimeoutError without cancelling the "
        "coroutine.  It's orphaned on the MCP loop and any lock or "
        "resource it acquired is leaked forever."
    )


def test_orphaned_call_does_not_leak_rpc_lock():
    """The production wedge: an orphan inside ``async with _rpc_lock``
    must not leak the lock — otherwise the next ``_call`` and the
    reconnect's ``_discover_tools`` block forever and only a hermes
    container restart unwedges it.
    """
    from tools import mcp_tool

    mcp_tool._ensure_mcp_loop()

    # Mirrors ``MCPServerTask._rpc_lock`` (tools/mcp_tool.py:900):
    # one lock per server, shared by every ``_call()`` (line 2028) and
    # by ``_discover_tools`` (line 1257) across reconnects.
    rpc_lock = asyncio.Lock()
    held_during_first = threading.Event()
    second_acquired = threading.Event()

    async def _call_hangs_inside_lock():
        async with rpc_lock:
            held_during_first.set()
            # Simulate ``await session.call_tool(...)`` stalling while
            # the MCP server is mid-swap.  Real ``call_tool`` doesn't
            # observe the swap promptly because raw connection errors
            # aren't delivered to the read stream by
            # ``streamable_http.post_writer``.
            await asyncio.sleep(60)

    async def _discover_tools_style_acquire():
        async with rpc_lock:
            second_acquired.set()

    # First call orphans inside the lock when the deadline fires.
    with pytest.raises(TimeoutError):
        mcp_tool._run_on_mcp_loop(_call_hangs_inside_lock(), timeout=0.3)
    assert held_during_first.is_set(), "first coroutine never reached the lock"

    # A fresh acquirer — modelling the reconnect's ``_discover_tools`` or
    # the next user tool call — must be able to take the lock once the
    # orphan is cancelled.
    try:
        mcp_tool._run_on_mcp_loop(_discover_tools_style_acquire(), timeout=2.0)
    except TimeoutError:
        pass  # bug present: still waiting on the leaked lock

    assert second_acquired.is_set(), (
        "Second coroutine could not acquire `_rpc_lock` after the first "
        "call timed out — the orphan inside `async with` still holds it.  "
        "This is the production wedge: every later tool call and the "
        "reconnect's `_discover_tools` block forever, and only a "
        "container restart escapes it."
    )
