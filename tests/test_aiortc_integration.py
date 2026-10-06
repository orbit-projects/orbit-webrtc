from __future__ import annotations

import asyncio
import importlib.util
import json

import pytest

pytestmark = pytest.mark.skipif(
    importlib.util.find_spec("aiortc") is None, reason="aiortc test extra unavailable"
)


@pytest.mark.asyncio
async def test_loopback_data_channel_round_trip() -> None:
    from aiortc import RTCPeerConnection, RTCSessionDescription
    from websockets.asyncio.client import connect

    from orbit_webrtc import WebRTCServer, WebRTCSettings

    echo_received = asyncio.Event()

    def on_data_channel(_peer: object, channel: object) -> None:
        @channel.on("message")  # type: ignore[attr-defined]
        def echo(message: str | bytes) -> None:
            channel.send(message)  # type: ignore[attr-defined]

    server = WebRTCServer(
        WebRTCSettings(port=0),
        on_data_channel=on_data_channel,  # type: ignore[arg-type]
    )
    await server.start()
    assert server.address is not None
    host, port = server.address
    client = RTCPeerConnection()
    channel = client.createDataChannel("echo")
    channel_open = asyncio.Event()

    @channel.on("open")
    def opened() -> None:
        channel_open.set()

    @channel.on("message")
    def received(message: str | bytes) -> None:
        if message == "ping":
            echo_received.set()

    try:
        offer = await client.createOffer()
        await client.setLocalDescription(offer)
        assert client.localDescription is not None
        async with connect(f"ws://{host}:{port}/rtc") as signaling:
            await signaling.send(
                json.dumps({"v": 1, "type": "offer", "sdp": client.localDescription.sdp})
            )
            answer = json.loads(await asyncio.wait_for(signaling.recv(), 30))
            await client.setRemoteDescription(
                RTCSessionDescription(sdp=answer["sdp"], type=answer["type"])
            )
            await asyncio.wait_for(channel_open.wait(), 30)
            channel.send("ping")
            await asyncio.wait_for(echo_received.wait(), 30)
    finally:
        await client.close()
        await server.close()
