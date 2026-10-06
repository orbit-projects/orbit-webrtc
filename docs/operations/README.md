# Operations

Bind the signaling server to a private interface and terminate HTTPS/WSS at a trusted ingress.
Allow the configured WebSocket path and preserve Origin. Ensure deployment networking permits the
ICE candidate transports and UDP flows required by the configured ICE/TURN topology. A working
signaling WebSocket does not prove media connectivity.

Set `max_peers` from memory, file descriptor, CPU, and media workload measurements. Configure
`max_sdp_bytes`, offer deadlines, shutdown deadlines, and ingress idle timeouts deliberately. Drain
signaling sessions before termination; the server closes each peer connection as its lease ends.
Provide TURN credentials through a short-lived secret mechanism. Test the exact browsers, codecs,
NATs, and ingress used in production.
