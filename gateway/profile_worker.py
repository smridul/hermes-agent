"""One Hermes profile-worker subprocess plus correlation-id-based dispatch.

The ingress process owns one :class:`ProfileWorker` per non-primary profile
listed in ``channels.whatsapp.profile_routing.profiles``.  The worker is a
fresh Hermes process started with ``HERMES_HOME=<profile path>`` and only
the IPC platform adapter attached.

Wire format (newline-delimited JSON, see also ``gateway/platforms/ipc.py``):

  Outbound (ingress -> worker)::

      {"kind": "message_event", "correlation_id": "<uuid>", "event": {...}}

  Inbound (worker -> ingress)::

      {"kind": "ready",  "name": "<profile>"}
      {"kind": "reply",  "correlation_id": "<uuid>", "reply": {...}}
"""

from __future__ import annotations

import asyncio
import json
import logging
import uuid
from typing import Any, Optional

logger = logging.getLogger(__name__)

# How long to wait for the worker's readiness signal before declaring it
# dead.  Cold Hermes boot can take a few seconds; 30 s is generous.
DEFAULT_READY_TIMEOUT = 30.0


class ProfileWorkerError(RuntimeError):
    """Raised when a dispatch fails (worker died, timeout, malformed reply)."""


class ProfileWorker:
    """Wrapper around one worker subprocess.

    Public API::

        await worker.start()
        reply = await worker.dispatch(event_dict, timeout=...)
        await worker.stop()
    """

    def __init__(
        self,
        name: str,
        argv: list[str],
        env: dict[str, str],
        *,
        ready_timeout: float = DEFAULT_READY_TIMEOUT,
    ) -> None:
        self.name = name
        self._argv = list(argv)
        self._env = dict(env)
        self._ready_timeout = ready_timeout
        self._proc: Optional[asyncio.subprocess.Process] = None
        self._reader_task: Optional[asyncio.Task] = None
        self._stderr_task: Optional[asyncio.Task] = None
        self._pending: dict[str, asyncio.Future] = {}
        self._ready: asyncio.Event = asyncio.Event()
        self._stopping: bool = False

    async def start(self) -> None:
        if self._proc is not None:
            return
        self._proc = await asyncio.create_subprocess_exec(
            *self._argv,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env={**self._env},
        )
        self._reader_task = asyncio.create_task(
            self._read_loop(), name=f"profile_worker_reader[{self.name}]"
        )
        self._stderr_task = asyncio.create_task(
            self._drain_stderr(), name=f"profile_worker_stderr[{self.name}]"
        )

        # Wait for either the ready signal or the worker dying.
        ready_wait = asyncio.create_task(self._ready.wait())
        proc_wait = asyncio.create_task(self._proc.wait())
        try:
            done, _pending = await asyncio.wait(
                [ready_wait, proc_wait],
                timeout=self._ready_timeout,
                return_when=asyncio.FIRST_COMPLETED,
            )
        finally:
            for t in (ready_wait, proc_wait):
                if not t.done():
                    t.cancel()

        if proc_wait in done and not self._ready.is_set():
            await self.stop()
            raise ProfileWorkerError(
                f"worker {self.name!r} exited before emitting readiness "
                f"(returncode={self._proc.returncode})"
            )
        if not self._ready.is_set():
            await self.stop()
            raise ProfileWorkerError(
                f"worker {self.name!r} did not emit readiness signal in "
                f"{self._ready_timeout}s"
            )

    async def stop(self) -> None:
        self._stopping = True
        if self._proc is not None and self._proc.returncode is None:
            try:
                if self._proc.stdin is not None:
                    self._proc.stdin.close()
            except Exception:
                pass
            try:
                await asyncio.wait_for(self._proc.wait(), timeout=5.0)
            except asyncio.TimeoutError:
                self._proc.kill()
                await self._proc.wait()

        for task in (self._reader_task, self._stderr_task):
            if task is not None:
                task.cancel()
                try:
                    await task
                except (asyncio.CancelledError, Exception):
                    pass

        for fut in self._pending.values():
            if not fut.done():
                fut.set_exception(
                    ProfileWorkerError(f"worker {self.name!r} stopped")
                )
        self._pending.clear()

    async def dispatch(
        self,
        event_dict: dict[str, Any],
        *,
        timeout: float = 300.0,
    ) -> dict[str, Any]:
        if self._proc is None or self._proc.returncode is not None:
            raise ProfileWorkerError(f"worker {self.name!r} not running")

        correlation_id = uuid.uuid4().hex
        envelope = {
            "kind": "message_event",
            "correlation_id": correlation_id,
            "event": event_dict,
        }
        loop = asyncio.get_running_loop()
        future: asyncio.Future = loop.create_future()
        self._pending[correlation_id] = future

        try:
            line = json.dumps(envelope) + "\n"
            self._proc.stdin.write(line.encode("utf-8"))  # type: ignore[union-attr]
            await self._proc.stdin.drain()  # type: ignore[union-attr]
        except Exception as exc:
            self._pending.pop(correlation_id, None)
            raise ProfileWorkerError(
                f"failed to write to worker {self.name!r}: {exc}"
            ) from exc

        try:
            return await asyncio.wait_for(future, timeout=timeout)
        finally:
            self._pending.pop(correlation_id, None)

    async def _read_loop(self) -> None:
        assert self._proc is not None and self._proc.stdout is not None
        try:
            while True:
                line = await self._proc.stdout.readline()
                if not line:
                    break
                text = line.decode("utf-8", errors="replace").strip()
                if not text:
                    continue
                try:
                    envelope = json.loads(text)
                except json.JSONDecodeError as exc:
                    logger.warning(
                        "worker %s: dropped non-JSON stdout line %r (%s)",
                        self.name,
                        text[:200],
                        exc,
                    )
                    continue
                kind = envelope.get("kind")
                if kind == "ready":
                    self._ready.set()
                elif kind == "reply":
                    cid = envelope.get("correlation_id")
                    fut = self._pending.get(cid) if cid else None
                    if fut is None or fut.done():
                        logger.warning(
                            "worker %s: reply for unknown correlation_id %r",
                            self.name,
                            cid,
                        )
                        continue
                    fut.set_result(envelope.get("reply") or {})
                else:
                    logger.warning(
                        "worker %s: unknown envelope kind %r", self.name, kind
                    )
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("worker %s: reader loop crashed", self.name)
        finally:
            if not self._stopping:
                for fut in self._pending.values():
                    if not fut.done():
                        fut.set_exception(
                            ProfileWorkerError(
                                f"worker {self.name!r} stdout closed unexpectedly"
                            )
                        )
                self._pending.clear()

    async def _drain_stderr(self) -> None:
        """Forward worker stderr to the gateway logger.

        Worker stdout is the IPC channel; nothing else may write to it.  All
        worker logging is configured to go to stderr, which we drain here so
        operators can see worker output during debugging.
        """
        assert self._proc is not None and self._proc.stderr is not None
        try:
            while True:
                line = await self._proc.stderr.readline()
                if not line:
                    break
                text = line.decode("utf-8", errors="replace").rstrip()
                if text:
                    logger.info("worker[%s] stderr: %s", self.name, text)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("worker[%s] stderr drainer crashed", self.name)
