# Operations

Bind the signaling server to a private interface and terminate HTTPS/WSS at a trusted ingress.
Allow the configured WebSocket path and preserve Origin. Ensure deployment networking permits the
ICE candidate transports and UDP flows required by the configured ICE/TURN topology. A working
signaling WebSocket does not prove media connectivity.

Set `max_peers` from memory, file descriptor, CPU, and media workload measurements. The default is
64 and the package hard ceiling is 4,096 per process. Media sections are capped at 16 by default
(64 maximum); data channels are capped at 32 by default (256 maximum). These are per-process limits;
replicas multiply total peer and media capacity. Configure `max_sdp_bytes`, offer deadlines,
shutdown deadlines, and ingress idle timeouts deliberately. The SDP media-section count is checked
before an aiortc peer is created, but aiortc still parses bounded SDP up to the configured byte
limit.

Drain signaling sessions before termination; the server closes each peer connection as its lease
ends. Provide TURN credentials through a short-lived secret mechanism. Test the exact browsers,
codecs, NATs, UDP path, TURN setup, and ingress used in production. Signaling success alone does not
prove that ICE connectivity or media flow succeeds.
