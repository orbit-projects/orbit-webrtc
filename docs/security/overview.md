# Security

The listener defaults to loopback and accepts only absent Origin until exact allowed origins are
configured. Provide an `authorize` callback for application identity before upgrade. Never accept
arbitrary public offers without authorization and abuse controls. The callback must return the
boolean value `True` to allow a connection; other truthy values fail closed. Bounds cover signaling
frame/SDP bytes, media sections, data channels, peers, origins, and ICE configuration. These limits
are per process. Rejecting excessive media sections before peer creation reduces work, but does not
make untrusted SDP harmless or replace ingress rate limiting. Do not log SDP, ICE candidates, TURN
credentials, auth headers, or media/data payloads.

WebRTC transport encryption does not provide application identity or authorization. Use a trusted
TLS signaling endpoint, short-lived ICE/TURN credentials, network-level rate limiting, and per-user
peer quotas. Keep TURN secrets out of source, command-line arguments, reprs, and telemetry.
