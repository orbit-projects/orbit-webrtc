"""Standalone WebRTC signaling and peer lifecycle using aiortc."""

from __future__ import annotations

import asyncio
import json
import logging
import math
import secrets
from collections.abc import Awaitable, Callable
from contextlib import suppress
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any
from urllib.parse import urlsplit

if TYPE_CHECKING:
    from websockets.asyncio.server import Server, ServerConnection
    from websockets.datastructures import Headers

logger = logging.getLogger(__name__)
MAX_SIGNAL_BYTES = 256 * 1024


@dataclass(frozen=True, slots=True, repr=False)
class ICEConfiguration:
    """One explicitly configured STUN/TURN server; credential values are secret."""

    urls: tuple[str, ...]
    username: str | None = None
    credential: str | None = field(default=None, repr=False)

    def __post_init__(self) -> None:
        if not isinstance(self.urls, tuple) or not self.urls:
            raise ValueError("ICE server URLs must be a non-empty immutable tuple.")
        for url in self.urls:
            parsed = urlsplit(url)
            if (
                parsed.scheme not in {"stun", "stuns", "turn", "turns"}
                or not (parsed.netloc or parsed.path)
                or "@" in url
            ):
                raise ValueError(
                    "ICE URLs must use STUN/TURN schemes without embedded credentials."
                )
        if (self.username is None) != (self.credential is None):
            raise ValueError("TURN username and credential must be configured together.")
        if self.username is not None and (not self.username or not self.credential):
            raise ValueError("TURN username and credential must be non-empty.")

    def __repr__(self) -> str:
        return (
            f"ICEConfiguration(url_count={len(self.urls)}, "
            f"credentials_configured={self.credential is not None})"
        )


@dataclass(frozen=True, slots=True)
class WebRTCSettings:
    """Signaling listener limits and ICE configuration for the server's peer connections."""

    host: str = "127.0.0.1"
    port: int = 8766
    path: str = "/rtc"
    origins: tuple[str | None, ...] = (None,)
    ice_servers: tuple[ICEConfiguration, ...] = ()
    max_peers: int = 64
    max_media_sections: int = 16
    max_data_channels: int = 32
    max_sdp_bytes: int = 192 * 1024
    offer_timeout_seconds: float = 20.0
    close_timeout_seconds: float = 5.0
    shutdown_timeout_seconds: float = 10.0
    ping_interval_seconds: float = 20.0

    def __post_init__(self) -> None:
        if not self.host or "\x00" in self.host:
            raise ValueError("host must be a non-empty bind address.")
        if (
            isinstance(self.port, bool)
            or not isinstance(self.port, int)
            or not 0 <= self.port <= 65535
        ):
            raise ValueError("port must be between 0 and 65535.")
        if not self.path.startswith("/") or "?" in self.path or "#" in self.path:
            raise ValueError("path must be an absolute path without query or fragment.")
        if not isinstance(self.origins, tuple) or not self.origins:
            raise ValueError("origins must explicitly allow one or more browser origins.")
        for origin in self.origins:
            if origin is None:
                continue
            parsed = urlsplit(origin)
            if (
                parsed.scheme not in {"http", "https"}
                or not parsed.hostname
                or parsed.path
                or parsed.query
                or parsed.fragment
                or parsed.username
                or parsed.password
            ):
                raise ValueError("each origin must be an exact HTTP(S) origin.")
        if isinstance(self.max_peers, bool) or not 1 <= self.max_peers <= 4096:
            raise ValueError("max_peers must be between 1 and 4096.")
        if isinstance(self.max_media_sections, bool) or not 1 <= self.max_media_sections <= 64:
            raise ValueError("max_media_sections must be between 1 and 64.")
        if isinstance(self.max_data_channels, bool) or not 1 <= self.max_data_channels <= 256:
            raise ValueError("max_data_channels must be between 1 and 256.")
        if (
            isinstance(self.max_sdp_bytes, bool)
            or not 1024 <= self.max_sdp_bytes <= MAX_SIGNAL_BYTES
        ):
            raise ValueError("max_sdp_bytes must be between 1 KiB and 256 KiB.")
        for name in (
            "offer_timeout_seconds",
            "close_timeout_seconds",
            "shutdown_timeout_seconds",
            "ping_interval_seconds",
        ):
            value = getattr(self, name)
            if (
                isinstance(value, bool)
                or not isinstance(value, (int, float))
                or not math.isfinite(value)
                or not 0.1 <= value <= 300
            ):
                raise ValueError(f"{name} must be finite and between 0.1 and 300 seconds.")


@dataclass(slots=True)
class PeerSession:
    """One accepted peer. ``connection`` is the native aiortc peer for media operations."""

    id: str
    connection: Any


Authorize = Callable[["Headers"], Awaitable[bool]]
PeerCallback = Callable[[PeerSession], None]
TrackCallback = Callable[[PeerSession, Any], None]
DataChannelCallback = Callable[[PeerSession, Any], None]


class WebRTCServer:
    """Own a bounded WebSocket signaling listener and its aiortc peer connections."""

    def __init__(
        self,
        settings: WebRTCSettings | None = None,
        *,
        authorize: Authorize | None = None,
        on_peer: PeerCallback | None = None,
        on_track: TrackCallback | None = None,
        on_data_channel: DataChannelCallback | None = None,
    ) -> None:
        self.settings = WebRTCSettings() if settings is None else settings
        self._authorize = authorize
        self._on_peer = on_peer
        self._on_track = on_track
        self._on_data_channel = on_data_channel
        self._server: Server | None = None
        self._peers: dict[str, PeerSession] = {}
        self._tasks: set[asyncio.Task[None]] = set()
        self._lock = asyncio.Lock()
        self._stopping = False
        self._rtc_configuration: Any = None
        self._reserved = 0

    @property
    def address(self) -> tuple[str, int] | None:
        """Return the bound listener address after start, including an ephemeral port."""
        if self._server is None or not self._server.sockets:
            return None
        host, port, *_ = self._server.sockets[0].getsockname()
        return str(host), int(port)

    @property
    def active_peers(self) -> int:
        """Return the current peer count."""
        return len(self._peers)

    async def start(self) -> None:
        """Bind the signaling listener. Install aiortc and websockets with this package."""
        if self._server is not None:
            raise RuntimeError("WebRTC server is already started.")
        from aiortc import RTCConfiguration, RTCIceServer
        from websockets.asyncio.server import serve
        from websockets.http11 import Response
        from websockets.typing import Origin

        ice = [
            RTCIceServer(
                urls=list(server.urls), username=server.username, credential=server.credential
            )
            for server in self.settings.ice_servers
        ]
        configuration = RTCConfiguration(iceServers=ice)

        async def process_request(connection: ServerConnection, request: Any) -> Response | None:
            if request.path.split("?", 1)[0] != self.settings.path:
                return connection.respond(404, "Not found\n")
            if self._authorize is not None:
                try:
                    allowed = await self._authorize(request.headers)
                except Exception:
                    logger.warning("WebRTC signaling authorization callback failed")
                    allowed = False
                if not allowed:
                    return connection.respond(401, "Unauthorized\n")
            return None

        self._stopping = False
        self._server = await serve(
            self._handle,
            self.settings.host,
            self.settings.port,
            origins=tuple(
                None if origin is None else Origin(origin) for origin in self.settings.origins
            ),
            process_request=process_request,
            max_size=MAX_SIGNAL_BYTES,
            max_queue=2,
            ping_interval=self.settings.ping_interval_seconds,
            ping_timeout=self.settings.ping_interval_seconds,
            close_timeout=self.settings.close_timeout_seconds,
            compression=None,
            server_header=None,
        )
        self._rtc_configuration = configuration

    async def serve_forever(self) -> None:
        """Run until cancelled or the listener is closed."""
        if self._server is None:
            raise RuntimeError("WebRTC server is not started.")
        await self._server.serve_forever()

    async def _handle(self, websocket: ServerConnection) -> None:
        task = asyncio.current_task()
        if task is not None:
            self._tasks.add(task)
        peer: PeerSession | None = None
        reserved = False
        try:
            async with self._lock:
                if self._stopping or len(self._peers) + self._reserved >= self.settings.max_peers:
                    capacity_reached = True
                else:
                    self._reserved += 1
                    reserved = True
                    capacity_reached = False
            if capacity_reached:
                await websocket.close(code=1013, reason="peer capacity reached")
                return
            first = await asyncio.wait_for(
                websocket.recv(), timeout=self.settings.offer_timeout_seconds
            )
            if not isinstance(first, str):
                await websocket.close(code=1003, reason="text offer required")
                return
            try:
                offer = self._decode_offer(first)
            except ValueError:
                await websocket.close(code=1008, reason="invalid WebRTC offer")
                return
            from aiortc import RTCPeerConnection, RTCSessionDescription

            connection = RTCPeerConnection(configuration=self._rtc_configuration)
            peer = PeerSession(secrets.token_urlsafe(18), connection)
            async with self._lock:
                if self._stopping:
                    await connection.close()
                    await websocket.close(code=1013, reason="peer capacity reached")
                    return
                self._peers[peer.id] = peer
                self._reserved -= 1
                reserved = False
            if self._on_peer is not None:
                self._on_peer(peer)
            self._wire_callbacks(peer)
            async with asyncio.timeout(self.settings.offer_timeout_seconds):
                await connection.setRemoteDescription(
                    RTCSessionDescription(sdp=offer, type="offer")
                )
                if len(connection.getTransceivers()) > self.settings.max_media_sections:
                    await websocket.close(code=1008, reason="media section limit exceeded")
                    return
                answer = await connection.createAnswer()
                await connection.setLocalDescription(answer)
            local = connection.localDescription
            if local is None or len(local.sdp.encode("utf-8")) > self.settings.max_sdp_bytes:
                await websocket.close(code=1009, reason="answer exceeds configured limit")
                return
            await websocket.send(
                json.dumps({"v": 1, "type": "answer", "sdp": local.sdp}, separators=(",", ":"))
            )
            async for raw in websocket:
                if not isinstance(raw, str):
                    await websocket.close(code=1003, reason="text signaling required")
                    break
                try:
                    message = json.loads(raw)
                except (json.JSONDecodeError, ValueError):
                    await websocket.close(code=1007, reason="invalid signaling JSON")
                    break
                if message != {"v": 1, "type": "close"}:
                    await websocket.close(code=1008, reason="unsupported signaling message")
                    break
                break
        except TimeoutError:
            with suppress(Exception):
                await websocket.close(code=1008, reason="signaling deadline exceeded")
        except Exception:
            logger.debug("WebRTC peer session ended")
            with suppress(Exception):
                await websocket.close(code=1011, reason="peer session failed")
        finally:
            if peer is not None:
                self._peers.pop(peer.id, None)
                try:
                    async with asyncio.timeout(self.settings.close_timeout_seconds):
                        await peer.connection.close()
                except Exception:
                    logger.debug("WebRTC peer cleanup exceeded its deadline")
            if reserved:
                async with self._lock:
                    self._reserved -= 1
            if task is not None:
                self._tasks.discard(task)

    def _decode_offer(self, raw: str) -> str:
        if len(raw.encode("utf-8")) > MAX_SIGNAL_BYTES:
            raise ValueError("signaling message exceeds transport limit")
        try:
            message = json.loads(raw)
        except (json.JSONDecodeError, ValueError):
            raise ValueError("signaling message is invalid") from None
        if not isinstance(message, dict) or set(message) != {"v", "type", "sdp"}:
            raise ValueError("offer must contain exactly v, type, and sdp")
        sdp = message["sdp"]
        if message["v"] != 1 or message["type"] != "offer" or not isinstance(sdp, str):
            raise ValueError("signaling message is not a version 1 offer")
        if len(sdp.encode("utf-8")) > self.settings.max_sdp_bytes:
            raise ValueError("SDP exceeds configured limit")
        return sdp

    def _wire_callbacks(self, peer: PeerSession) -> None:
        connection = peer.connection

        @connection.on("track")  # type: ignore[untyped-decorator]
        def on_track(track: Any) -> None:
            if self._on_track is not None:
                self._on_track(peer, track)
            else:
                task = asyncio.create_task(self._drain_track(track))
                self._tasks.add(task)
                task.add_done_callback(self._tasks.discard)

        data_channels = 0

        @connection.on("datachannel")  # type: ignore[untyped-decorator]
        def on_data_channel(channel: Any) -> None:
            nonlocal data_channels
            data_channels += 1
            if data_channels > self.settings.max_data_channels or self._on_data_channel is None:
                channel.close()
                return
            self._on_data_channel(peer, channel)

    @staticmethod
    async def _drain_track(track: Any) -> None:
        """Consume unhandled media to prevent receiver queues growing without bound."""
        try:
            while True:
                await track.recv()
        except Exception:
            return

    async def close(self) -> None:
        """Stop accepts, close peers, and wait for handlers up to configured deadlines."""
        self._stopping = True
        server = self._server
        if server is None:
            return
        server.close(close_connections=True)
        peers = tuple(self._peers.values())
        try:
            async with asyncio.timeout(self.settings.shutdown_timeout_seconds):
                await asyncio.gather(
                    *(peer.connection.close() for peer in peers), return_exceptions=True
                )
                await server.wait_closed()
                current = asyncio.current_task()
                handlers = [task for task in self._tasks if task is not current]
                if handlers:
                    await asyncio.gather(*handlers, return_exceptions=True)
        except TimeoutError:
            for task in tuple(self._tasks):
                if task is not asyncio.current_task():
                    task.cancel()
        finally:
            self._peers.clear()
            self._server = None
