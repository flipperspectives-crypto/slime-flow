"""Slime Flow HTTP client — connects to the Julia GPU simulation server."""

from __future__ import annotations

import json
import time
import urllib.request
import urllib.error
from typing import Optional, Iterator, AsyncIterator, Callable, Awaitable, Dict, Any, Union

from slimeflow.models import Frame, Status

DEFAULT_HOST = "localhost"
DEFAULT_PORT = 8080


class SlimeFlowError(Exception):
    """Base exception for Slime Flow client errors."""


class ConnectionError(SlimeFlowError):
    """Could not connect to the Slime Flow server."""


class ServerError(SlimeFlowError):
    """Server returned an error response."""


class SlimeFlow:
    """Client for the Slime Flow GPU simulation server.

    Args:
        host: Server hostname (default: localhost)
        port: Server port (default: 8080)
        timeout: HTTP request timeout in seconds (default: 10)

    Example::

        sf = SlimeFlow("localhost", 8080)

        # Get status
        status = sf.status()
        print(f"GPU: {status.gpu}, step: {status.step}")

        # Get a frame
        frame = sf.frame()
        print(f"Density: {frame.density():.3f}")

        # Inject chaos
        sf.spawn_rogues()
        sf.inject_fault(0.5, 0.5)

        # Stream frames
        for frame in sf.stream(interval=0.05):
            print(f"Step {frame.step}: {frame.rogue_count} rogues")
    """

    def __init__(
        self,
        host: str = DEFAULT_HOST,
        port: int = DEFAULT_PORT,
        timeout: float = 10.0,
    ):
        self.host = host
        self.port = port
        self.timeout = timeout
        self._base = f"http://{host}:{port}"

    # ─── HTTP helpers ────────────────────────────────────────────────────

    def _request(
        self,
        path: str,
        method: str = "GET",
        body: Optional[dict] = None,
        timeout: Optional[float] = None,
    ) -> bytes:
        """Low-level HTTP request. Returns raw response body bytes."""
        url = f"{self._base}{path}"
        data = None
        headers = {}

        if body is not None:
            data = json.dumps(body).encode("utf-8")
            headers["Content-Type"] = "application/json"

        req = urllib.request.Request(url, data=data, headers=headers, method=method)

        try:
            with urllib.request.urlopen(req, timeout=timeout or self.timeout) as resp:
                return resp.read()
        except urllib.error.HTTPError as e:
            raise ServerError(f"HTTP {e.code} on {path}: {e.reason}") from e
        except urllib.error.URLError as e:
            raise ConnectionError(f"Cannot reach {self._base}: {e}") from e

    def _get(self, path: str, timeout: Optional[float] = None) -> bytes:
        return self._request(path, "GET", timeout=timeout)

    def _post(
        self, path: str, body: dict, timeout: Optional[float] = None
    ) -> bytes:
        return self._request(path, "POST", body=body, timeout=timeout)

    def _get_json(self, path: str, timeout: Optional[float] = None) -> dict:
        return json.loads(self._get(path, timeout=timeout))

    # ─── API methods ─────────────────────────────────────────────────────

    def status(self) -> Status:
        """Get server status including GPU info and sim stats."""
        data = self._get_json("/status")
        return Status.from_json(data)

    def frame(self) -> Frame:
        """Advance one simulation step and return the frame.

        Each call to this method advances the simulation by one step.
        For continuous streaming, use :meth:`stream` instead.
        """
        data = self._get_json("/frame")
        return Frame.from_json(data)

    def reset(self) -> None:
        """Reset the simulation to initial state."""
        self._get("/reset")

    def spawn_rogues(self) -> None:
        """Inject rogue agents into the swarm.

        Converts up to 12 normal agents to rogue type.
        """
        self._get("/rogues")

    def inject_fault(
        self, x: Optional[float] = None, y: Optional[float] = None
    ) -> None:
        """Inject a fault zone at normalized coordinates (0–1).

        If x or y are None, random values in [0.3, 0.7] are chosen.
        Fault zones kill non-Guardian agents inside a 20-unit radius.

        Args:
            x: Normalized x coordinate (0–1)
            y: Normalized y coordinate (0–1)
        """
        import random

        if x is None:
            x = 0.3 + random.random() * 0.4
        if y is None:
            y = 0.3 + random.random() * 0.4

        self._post("/fault", {"x": x, "y": y})

    def clear_fault(self) -> None:
        """Clear the active fault zone."""
        self._get("/fault/clear")

    def ping(self) -> bool:
        """Check if the server is reachable. Returns True/False."""
        try:
            self._get("/status", timeout=3)
            return True
        except SlimeFlowError:
            return False

    # ─── Streaming ───────────────────────────────────────────────────────

    def stream(
        self,
        interval: float = 0.05,
        max_frames: Optional[int] = None,
        on_frame: Optional[Callable[[Frame], Any]] = None,
    ) -> Iterator[Frame]:
        """Stream frames from the simulation in a blocking iterator.

        Args:
            interval: Seconds between frame requests (default 0.05 = 20 FPS)
            max_frames: Stop after this many frames (None = infinite)
            on_frame: Optional callback(frame) called each frame

        Yields:
            Frame objects as the simulation advances

        Example::

            for frame in sf.stream(max_frames=100):
                if frame.rogue_count > 10:
                    print("Rogue swarm detected!")
                    break
        """
        count = 0
        try:
            while max_frames is None or count < max_frames:
                t0 = time.monotonic()
                frame = self.frame()
                count += 1

                if on_frame:
                    on_frame(frame)

                yield frame

                # Maintain frame interval
                elapsed = time.monotonic() - t0
                if elapsed < interval:
                    time.sleep(interval - elapsed)

        except KeyboardInterrupt:
            return

    # ─── Async support ───────────────────────────────────────────────────

    async def async_status(self) -> Status:
        """Async version of :meth:`status`. Requires ``httpx``."""
        return Status.from_json(await self._async_get_json("/status"))

    async def async_frame(self) -> Frame:
        """Async version of :meth:`frame`. Requires ``httpx``."""
        return Frame.from_json(await self._async_get_json("/frame"))

    async def async_reset(self) -> None:
        await self._async_get("/reset")

    async def async_spawn_rogues(self) -> None:
        await self._async_get("/rogues")

    async def async_inject_fault(
        self, x: Optional[float] = None, y: Optional[float] = None
    ) -> None:
        import random

        if x is None:
            x = 0.3 + random.random() * 0.4
        if y is None:
            y = 0.3 + random.random() * 0.4

        await self._async_post("/fault", {"x": x, "y": y})

    async def async_clear_fault(self) -> None:
        await self._async_get("/fault/clear")

    async def async_ping(self) -> bool:
        try:
            await self._async_get("/status")
            return True
        except SlimeFlowError:
            return False

    async def async_stream(
        self,
        interval: float = 0.05,
        max_frames: Optional[int] = None,
        on_frame: Optional[Callable[[Frame], Any]] = None,
    ) -> AsyncIterator[Frame]:
        """Async frame stream. Requires ``httpx``.

        Example::

            async for frame in sf.async_stream(max_frames=100):
                print(frame)
        """
        import asyncio

        count = 0
        try:
            while max_frames is None or count < max_frames:
                frame = await self.async_frame()
                count += 1

                if on_frame:
                    result = on_frame(frame)
                    if asyncio.iscoroutine(result):
                        await result

                yield frame
                await asyncio.sleep(interval)

        except asyncio.CancelledError:
            return

    # ─── Async HTTP internals ────────────────────────────────────────────

    def _ensure_httpx(self):
        try:
            import httpx  # noqa: F401
        except ImportError:
            raise ImportError(
                "Async methods require httpx. Install with: pip install httpx"
            )

    async def _async_get(self, path: str) -> bytes:
        self._ensure_httpx()
        import httpx

        async with httpx.AsyncClient(
            timeout=httpx.Timeout(self.timeout),
        ) as client:
            resp = await client.get(f"{self._base}{path}")
            resp.raise_for_status()
            return resp.content

    async def _async_post(self, path: str, body: dict) -> bytes:
        self._ensure_httpx()
        import httpx

        async with httpx.AsyncClient(
            timeout=httpx.Timeout(self.timeout),
        ) as client:
            resp = await client.post(
                f"{self._base}{path}", json=body
            )
            resp.raise_for_status()
            return resp.content

    async def _async_get_json(self, path: str) -> dict:
        raw = await self._async_get(path)
        return json.loads(raw)

    # ─── Context manager ─────────────────────────────────────────────────

    def __enter__(self) -> "SlimeFlow":
        return self

    def __exit__(self, *args) -> None:
        pass

    async def __aenter__(self) -> "SlimeFlow":
        return self

    async def __aexit__(self, *args) -> None:
        pass

    def __repr__(self) -> str:
        return f"SlimeFlow(host={self.host!r}, port={self.port})"
