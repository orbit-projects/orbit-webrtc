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
_MAX_ICE_URLS = 16
_MAX_ICE_SERVERS = 16
_MAX_ORIGINS = 64


def _validate_integer(value: object, name: str, minimum: int, maximum: int) -> None:
    """Require an actual bounded integer rather than a bool or in-range float."""
    if isinstance(value, bool) or not isinstance(value, int) or not minimum <= value <= maximum:
        raise ValueError(f"{name} must be an integer from {minimum} through {maximum}.")


def _normalize_origin(value: str) -> tuple[str, str, int | None]:
    """Validate an exact browser origin and normalize its scheme, host, and default port."""
    if (
        not isinstance(value, str)
        or not value.isascii()
        or len(value) > 512
        or any(ord(character) <= 32 or ord(character) >= 127 for character in value)
    ):
        raise ValueError("each origin must be a bounded ASCII HTTP(S) origin.")
    try:
        parsed = urlsplit(value)
        scheme = parsed.scheme.lower()
        hostname = parsed.hostname
        port = parsed.port
    except ValueError:
        raise ValueError("each origin must be a valid HTTP(S) origin.") from None
    if (
        scheme not in {"http", "https"}
        or not hostname
        or "*" in hostname
        or parsed.netloc.endswith(":")
        or parsed.path
        or parsed.query
        or parsed.fragment
        or parsed.username is not None
        or parsed.password is not None
        or not value.lower().startswith(f"{scheme}://")
    ):
        raise ValueError("each origin must be an exact HTTP(S) origin.")
    if port == (80 if scheme == "http" else 443):
        port = None
    return scheme, hostname.lower(), port


def _unique_json_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    """Reject duplicate signaling keys instead of silently accepting the last value."""
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate signaling object key")
        result[key] = value
    return result


@dataclass(frozen=True, slots=True, repr=False)
class ICEConfiguration:
    """One explicitly configured STUN/TURN server; credential values are secret."""

    urls: tuple[str, ...]
    username: str | None = None
    credential: str | None = field(default=None, repr=False)

    def __post_init__(self) -> None:
        if not isinstance(self.urls, tuple) or not 1 <= len(self.urls) <= _MAX_ICE_URLS:
            raise ValueError("ICE server URLs must be a non-empty immutable tuple.")
        for url in self.urls:
            if (
                not isinstance(url, str)
                or not url.isascii()
                or not 1 <= len(url) <= 2_048
                or any(ord(character) <= 32 or ord(character) >= 127 for character in url)
            ):
                raise ValueError("ICE server URLs must be bounded printable ASCII values.")
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
        if self.username is not None and (
            not isinstance(self.username, str)
            or not isinstance(self.credential, str)
            or not 1 <= len(self.username) <= 1_024
            or not 1 <= len(self.credential) <= 4_096
            or any(ord(character) < 32 or ord(character) == 127 for character in self.username)
            or any(ord(character) < 32 or ord(character) == 127 for character in self.credential)
        ):
            raise ValueError("TURN credentials must be bounded text without control characters.")

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
        if (
            not isinstance(self.host, str)
            or not self.host
            or len(self.host) > 255
            or any(ord(character) < 33 or ord(character) > 126 for character in self.host)
        ):
            raise ValueError("host must be a bounded printable ASCII bind address.")
        _validate_integer(self.port, "port", 0, 65535)
        if (
            not isinstance(self.path, str)
            or not self.path.startswith("/")
            or len(self.path) > 1_024
            or "?" in self.path
            or "#" in self.path
            or any(ord(character) < 33 or ord(character) > 126 for character in self.path)
        ):
            raise ValueError("path must be a bounded printable ASCII path without query/fragment.")
        if not isinstance(self.origins, tuple) or not 1 <= len(self.origins) <= _MAX_ORIGINS:
            raise ValueError("origins must explicitly allow one or more browser origins.")
        if not isinstance(self.ice_servers, tuple) or len(self.ice_servers) > _MAX_ICE_SERVERS:
            raise ValueError(f"ice_servers must be a tuple of at most {_MAX_ICE_SERVERS} entries.")
        origin_keys: set[tuple[str, str, int | None] | None] = set()
        for origin in self.origins:
            key = None if origin is None else _normalize_origin(origin)
            if key in origin_keys:
                raise ValueError("origins must be unique, including at most one no-Origin entry.")
            origin_keys.add(key)
        if any(not isinstance(item, ICEConfiguration) for item in self.ice_servers):
            raise TypeError("ice_servers must contain ICEConfiguration values.")
        _validate_integer(self.max_peers, "max_peers", 1, 4096)
        _validate_integer(self.max_media_sections, "max_media_sections", 1, 64)
        _validate_integer(self.max_data_channels, "max_data_channels", 1, 256)
        _validate_integer(self.max_sdp_bytes, "max_sdp_bytes", 1024, MAX_SIGNAL_BYTES)
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
        if settings is not None and not isinstance(settings, WebRTCSettings):
            raise TypeError("settings must be a WebRTCSettings instance.")
        for name, callback in (
            ("authorize", authorize),
            ("on_peer", on_peer),
            ("on_track", on_track),
            ("on_data_channel", on_data_channel),
        ):
            if callback is not None and not callable(callback):
                raise TypeError(f"{name} must be callable when provided.")
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
                if allowed is not True:
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
                    message = json.loads(raw, object_pairs_hook=_unique_json_object)
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
            message = json.loads(raw, object_pairs_hook=_unique_json_object)
        except (json.JSONDecodeError, ValueError):
            raise ValueError("signaling message is invalid") from None
        if not isinstance(message, dict) or set(message) != {"v", "type", "sdp"}:
            raise ValueError("offer must contain exactly v, type, and sdp")
        sdp = message["sdp"]
        if message["v"] != 1 or message["type"] != "offer" or not isinstance(sdp, str):
            raise ValueError("signaling message is not a version 1 offer")
        if len(sdp.encode("utf-8")) > self.settings.max_sdp_bytes:
            raise ValueError("SDP exceeds configured limit")
        if (
            sum(line.startswith("m=") for line in sdp.splitlines())
            > self.settings.max_media_sections
        ):
            raise ValueError("SDP exceeds configured media section limit")
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
