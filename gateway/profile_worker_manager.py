"""Owns the set of profile worker subprocesses; dispatches by profile name."""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from typing import Any

from gateway.profile_worker import ProfileWorker

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class WorkerSpec:
    name: str
    argv: list[str]
    env: dict[str, str] = field(default_factory=dict)


class ProfileWorkerManager:
    """Manages a fleet of :class:`ProfileWorker` instances keyed by profile name."""

    def __init__(self) -> None:
        self._workers: dict[str, ProfileWorker] = {}

    async def start(self, specs: list[WorkerSpec]) -> None:
        """Spawn all worker subprocesses in parallel.

        If any worker fails to come up, all started workers are torn down
        before re-raising.
        """

        async def _start_one(spec: WorkerSpec) -> tuple[str, ProfileWorker]:
            worker = ProfileWorker(name=spec.name, argv=spec.argv, env=spec.env)
            await worker.start()
            return spec.name, worker

        results = await asyncio.gather(
            *[_start_one(s) for s in specs],
            return_exceptions=True,
        )
        first_error: BaseException | None = None
        for spec, result in zip(specs, results):
            if isinstance(result, BaseException):
                first_error = first_error or result
                logger.error("Failed to start worker %s: %s", spec.name, result)
                continue
            name, worker = result  # type: ignore[misc]
            self._workers[name] = worker

        if first_error is not None:
            await self.shutdown()
            raise first_error

    async def shutdown(self) -> None:
        await asyncio.gather(
            *[w.stop() for w in self._workers.values()],
            return_exceptions=True,
        )
        self._workers.clear()

    async def dispatch(
        self,
        profile_name: str,
        event_dict: dict[str, Any],
        *,
        timeout: float = 300.0,
    ) -> dict[str, Any]:
        worker = self._workers.get(profile_name)
        if worker is None:
            raise KeyError(f"no worker registered for profile {profile_name!r}")
        return await worker.dispatch(event_dict, timeout=timeout)

    @property
    def names(self) -> list[str]:
        return list(self._workers.keys())
