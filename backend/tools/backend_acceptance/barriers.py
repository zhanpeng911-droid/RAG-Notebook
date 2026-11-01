"""File-based, bounded test-only process barriers with monotonic timestamps."""
from __future__ import annotations

import asyncio
import json
import os
import time
from pathlib import Path


class Barrier:
    def __init__(self, directory: Path, identity: str):
        if not identity or any(c not in "abcdefghijklmnopqrstuvwxyz0123456789-_" for c in identity):
            raise ValueError("invalid barrier identity")
        self.directory = directory.resolve()
        self.identity = identity
        self.directory.mkdir(parents=True, exist_ok=True)

    def mark(self, signal: str) -> None:
        if signal not in {"entered", "release", "finished"}:
            raise ValueError("invalid barrier signal")
        path = self.directory / f"{self.identity}.{signal}"
        payload = {"identity": self.identity, "signal": signal, "monotonic_ns": time.monotonic_ns(),
                   "pid": os.getpid()}
        # Atomic exclusive creation prevents stale signals being silently reused.
        with path.open("x", encoding="utf-8") as stream:
            json.dump(payload, stream)

    def wait(self, signal: str, timeout: float) -> dict:
        if signal not in {"entered", "release", "finished"} or timeout <= 0:
            raise ValueError("invalid signal or timeout")
        path = self.directory / f"{self.identity}.{signal}"
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if path.exists():
                try:
                    payload = json.loads(path.read_text(encoding="utf-8"))
                except (ValueError, OSError):
                    time.sleep(0.01)
                    continue
                if payload.get("identity") != self.identity or payload.get("signal") != signal:
                    raise ValueError("barrier identity mismatch")
                return payload
            time.sleep(0.01)
        raise TimeoutError(f"barrier {self.identity}.{signal} timed out")

    async def wait_async(self, signal: str, timeout: float) -> dict:
        return await asyncio.to_thread(self.wait, signal, timeout)
