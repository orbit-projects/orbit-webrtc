# Orbit WebRTC

`orbit-webrtc` is Orbit's Python server-side WebRTC peer implementation. It accepts one browser
offer over a bounded WebSocket signaling channel, returns a gathered SDP answer, and owns the
aiortc peer connection until signaling closes. Media and data travel over negotiated ICE/DTLS/SRTP
or SCTP paths; they do not pass through Core or through the signaling socket.

This v1 is a peer endpoint, not a multi-party SFU. It supports app callbacks for incoming media
tracks and data channels. Unhandled media tracks are drained and discarded to keep receiver queues
bounded. Configure STUN/TURN servers explicitly for deployed networks; there are no embedded
servers or default credentials. TURN credentials must be short-lived and delivered via your secret
management path.

## Install

```sh
python -m pip install orbit-webrtc
```

## Signaling contract

Connect to `/rtc` (or the configured path) using a WebSocket. Send one text JSON offer:

```json
{"v":1,"type":"offer","sdp":"<complete SDP offer with gathered ICE candidates>"}
```

The server replies with `{"v":1,"type":"answer","sdp":"..."}`. Version 1 uses complete
offer/answer SDP rather than trickle ICE; clients must finish local ICE gathering before sending
their offer. The signaling socket remains open for the lifetime of the peer and is its control
lease. Send `{"v":1,"type":"close"}` to close; disconnecting also closes the peer. Text frames
and SDP are bounded. The server does not log SDP, candidate addresses, TURN credentials, or payloads.

```python
from aiortc import RTCDataChannel
from orbit_webrtc import ICEConfiguration, WebRTCServer, WebRTCSettings


def on_data_channel(_peer, channel: RTCDataChannel) -> None:
    @channel.on("message")
    def echo(message: str | bytes) -> None:
        channel.send(message)


server = WebRTCServer(
    WebRTCSettings(
        origins=("https://app.example",),
        ice_servers=(
            ICEConfiguration(("turns:turn.example:5349",), "ephemeral-user", "injected-secret"),
        ),
    ),
    on_data_channel=on_data_channel,
)
```

`authorize` can enforce application authentication before the WebSocket upgrade. Use exact browser
origins and a TLS-terminating ingress. Authentication, tenant isolation, room membership, and
authorization policies remain application responsibilities.

## Operations and maturity

See [architecture](docs/architecture/overview.md), [operations](docs/operations/README.md),
[security](docs/security/overview.md), [development](docs/development/README.md), and the
[architecture decision records](docs/architecture/adr/README.md).

This package is pre-alpha. Validate browsers, codec negotiation, TURN/TLS, UDP reachability,
ingress timeouts, connection drain, and load in the target environment before release. It does not
implement an SFU, recording, transcoding, room coordination, or cross-instance signaling.

Licensed under Apache-2.0.
