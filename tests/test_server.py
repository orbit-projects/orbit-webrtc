from __future__ import annotations

import asyncio
import json
import sys
import types
from dataclasses import dataclass
from typing import Any

import pytest

from orbit_webrtc import WebRTCServer, WebRTCSettings


@dataclass
class Description:
    sdp: str
    type: str


class FakePeerConnection:
    instances: list[FakePeerConnection] = []

    def __init__(self, **_: Any) -> None:
        self.localDescription: Description | None = None
        self.callbacks: dict[str, Any] = {}
        self.closed = False
        self.instances.append(self)

    def on(self, name: str):
        def register(callback: Any) -> Any:
            self.callbacks[name] = callback
            return callback

        return register

    async def setRemoteDescription(self, description: Description) -> None:
        assert description.type == "offer"
        assert description.sdp == "v=0"

    async def createAnswer(self) -> Description:
        return Description("v=0\r\na=answer", "answer")

    async def setLocalDescription(self, description: Description) -> None:
        self.localDescription = description

    def getTransceivers(self) -> list[object]:
        return []

    async def close(self) -> None:
        self.closed = True


def fake_aiortc() -> types.ModuleType:
    module = types.ModuleType("aiortc")
    module.RTCConfiguration = lambda **kwargs: kwargs  # type: ignore[attr-defined]
    module.RTCIceServer = lambda **kwargs: kwargs  # type: ignore[attr-defined]
    module.RTCPeerConnection = FakePeerConnection  # type: ignore[attr-defined]
    module.RTCSessionDescription = Description  # type: ignore[attr-defined]
    return module


@pytest.mark.asyncio
async def test_signaling_answer_and_disconnect_close_peer(monkeypatch: pytest.MonkeyPatch) -> None:
    from websockets.asyncio.client import connect

    FakePeerConnection.instances.clear()
    monkeypatch.setitem(sys.modules, "aiortc", fake_aiortc())
    server = WebRTCServer(WebRTCSettings(port=0, origins=("https://app.example",)))
    await server.start()
    assert server.address is not None
    host, port = server.address
    try:
        async with connect(f"ws://{host}:{port}/rtc", origin="https://app.example") as websocket:
            await websocket.send(json.dumps({"v": 1, "type": "offer", "sdp": "v=0"}))
            answer = json.loads(await websocket.recv())
            assert answer == {"v": 1, "type": "answer", "sdp": "v=0\r\na=answer"}
            assert server.active_peers == 1
        await asyncio.sleep(0)
        assert FakePeerConnection.instances[0].closed
    finally:
        await server.close()


@pytest.mark.asyncio
async def test_malformed_offer_is_rejected_without_creating_peer(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from websockets.asyncio.client import connect
    from websockets.exceptions import ConnectionClosed

    FakePeerConnection.instances.clear()
    monkeypatch.setitem(sys.modules, "aiortc", fake_aiortc())
    server = WebRTCServer(WebRTCSettings(port=0))
    await server.start()
    assert server.address is not None
    host, port = server.address
    try:
        async with connect(f"ws://{host}:{port}/rtc") as websocket:
            await websocket.send('{"v":1,"type":"answer","sdp":"v=0"}')
            with pytest.raises(ConnectionClosed) as closed:
                await websocket.recv()
            assert closed.value.rcvd is not None and closed.value.rcvd.code == 1008
        assert not FakePeerConnection.instances
    finally:
        await server.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("authorization_result", [False, 1, None])
async def test_authorization_requires_explicit_true_before_upgrade(
    monkeypatch: pytest.MonkeyPatch, authorization_result: object
) -> None:
    from websockets.asyncio.client import connect
    from websockets.exceptions import InvalidStatus

    monkeypatch.setitem(sys.modules, "aiortc", fake_aiortc())

    async def deny(_headers: Any) -> bool:
        return authorization_result  # type: ignore[return-value]

    server = WebRTCServer(WebRTCSettings(port=0), authorize=deny)
    await server.start()
    assert server.address is not None
    host, port = server.address
    try:
        with pytest.raises(InvalidStatus) as denied:
            await connect(f"ws://{host}:{port}/rtc")
        assert denied.value.response.status_code == 401
    finally:
        await server.close()
