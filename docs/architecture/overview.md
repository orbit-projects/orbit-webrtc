# Architecture

`orbit-webrtc` owns Python peer-connection creation, offer/answer negotiation, ICE configuration,
and peer lifecycle. A WebSocket carries bounded versioned signaling JSON; the negotiated WebRTC
transport carries media and data directly between endpoints. Core does not relay frames or own
peer connections.

Version 1 is one server-side peer per signaling connection. It exchanges a complete SDP offer and
answer after ICE gathering; trickle candidates, SFU routing, room membership, and distributed peer
coordination are out of scope. Applications provide authorization and optional native aiortc
callbacks for tracks and data channels. Go's `orbit-webrtc-go` implements the same wire contract
using Pion.
