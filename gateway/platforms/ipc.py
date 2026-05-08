"""IPC platform adapter — the profile worker's only inbound source.

Reads newline-delimited JSON envelopes from stdin, dispatches each to the
gateway's message handler, and writes one reply envelope per message to
stdout.  The wire format is documented in
``docs/superpowers/specs/2026-05-07-whatsapp-sender-profile-routing-design.md``.

Inbound envelope::

    {
      "kind": "message_event",
      "correlation_id": "<uuid>",
      "event": { ... encoded MessageEvent ... }
    }

Outbound envelope::

    {
      "kind": "reply",
      "correlation_id": "<uuid>",
      "reply": {"text": "...", "error": null, "media": []}
    }

Stdout is the IPC channel; nothing else may write to it.  All worker
logging is routed to stderr.
"""

from __future__ import annotations

import asyncio
import json
import logging
import sys
import traceback
from typing import Any, Optional, TextIO

from gateway.config import Platform, PlatformConfig
from gateway.message_event_codec import decode_event
from gateway.platforms.base import BasePlatformAdapter, SendResult

logger = logging.getLogger(__name__)


class IPCPlatformAdapter(BasePlatformAdapter):
    """Adapter that uses stdin/stdout JSON pipes as its transport."""

    def __init__(
        self,
        config: PlatformConfig,
        stdin: Optional[TextIO] = None,
        stdout: Optional[TextIO] = None,
    ) -> None:
        super().__init__(config, Platform.IPC)
        self._stdin = stdin if stdin is not None else sys.stdin
        self._stdout = stdout if stdout is not None else sys.stdout

    @property
    def name(self) -> str:
        return "ipc"

    async def connect(self) -> bool:
        """Pump stdin lines until EOF, dispatching each to the handler."""
        self._mark_connected()
        loop = asyncio.get_event_loop()
        try:
            while True:
                # ``readline`` blocks on real stdin pipes; offload to a
                # thread so the event loop can keep doing other work.
                line = await loop.run_in_executor(None, self._stdin.readline)
                if not line:
                    break  # EOF
                line = line.strip()
                if not line:
                    continue
                await self._handle_line(line)
        finally:
            self._mark_disconnected()
        return True

    async def disconnect(self) -> None:
        # No-op: ``connect()`` exits naturally when stdin closes.
        self._mark_disconnected()

    async def get_chat_info(self, chat_id: str) -> dict:
        """Not meaningful for IPC — return a stub.

        Workers don't talk to a real chat platform; chat metadata that
        downstream code may inspect was already populated on the inbound
        ``MessageEvent.source`` by the ingress adapter.
        """
        return {"name": chat_id, "type": "dm"}

    async def send(
        self,
        chat_id: str,
        content: str,
        reply_to: Optional[str] = None,
        metadata: Optional[dict] = None,
    ) -> SendResult:
        """Replies are emitted from the inbound loop, not from ``send()``.

        ``send()`` is part of the abstract interface but is unused for IPC
        because the worker doesn't initiate outbound messages — it only
        responds to events received on stdin.
        """
        return SendResult(
            success=False,
            error=(
                "IPCPlatformAdapter.send() is not supported; replies are "
                "emitted from the handler return value via stdout"
            ),
        )

    async def _handle_line(self, line: str) -> None:
        try:
            envelope = json.loads(line)
        except json.JSONDecodeError as exc:
            logger.warning("ipc: malformed JSON (dropped): %s", exc)
            return

        kind = envelope.get("kind")
        if kind != "message_event":
            logger.warning("ipc: unknown envelope kind %r (dropped)", kind)
            return

        correlation_id = envelope.get("correlation_id")
        event_data = envelope.get("event") or {}

        try:
            event = decode_event(event_data)
        except Exception as exc:  # decode failure
            self._emit_reply(correlation_id, text=None, error=f"decode failed: {exc}")
            return

        handler = self._message_handler
        if handler is None:
            self._emit_reply(correlation_id, text=None, error="no handler set")
            return

        try:
            reply = await handler(event)
        except Exception as exc:
            tb = traceback.format_exc(limit=4)
            logger.exception("ipc: handler raised")
            self._emit_reply(correlation_id, text=None, error=f"{exc}\n{tb}")
            return

        if reply is None:
            text: Optional[str] = None
        elif isinstance(reply, str):
            text = reply
        else:
            text = getattr(reply, "text", None) or str(reply)
        self._emit_reply(correlation_id, text=text, error=None)

    def _emit_reply(
        self,
        correlation_id: Optional[str],
        *,
        text: Optional[str],
        error: Optional[str],
    ) -> None:
        envelope: dict[str, Any] = {
            "kind": "reply",
            "correlation_id": correlation_id,
            "reply": {"text": text, "error": error, "media": []},
        }
        line = json.dumps(envelope, ensure_ascii=False)
        self._stdout.write(line + "\n")
        self._stdout.flush()
