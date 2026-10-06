# Security

The listener defaults to loopback and accepts only absent Origin until exact allowed origins are
configured. Provide an `authorize` callback for application identity before upgrade. Never accept
arbitrary public offers without authorization and abuse controls. Bound SDP and peer count; do not
log SDP, ICE candidates, TURN credentials, auth headers, or media/data payloads.

WebRTC transport encryption does not provide application identity or authorization. Use a trusted
TLS signaling endpoint, short-lived ICE/TURN credentials, network-level rate limiting, and per-user
peer quotas. Keep TURN secrets out of source, command-line arguments, reprs, and telemetry.
