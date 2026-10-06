# 0001: Use WebSocket signaling for bounded server-side peers

- Status: accepted
- Date: 2026-10-06

## Context

WebRTC peer connections require signaling, ICE configuration, and deterministic resource ownership.
Core's process-plugin gRPC transport is not browser signaling and must not relay media packets.

## Decision

The Python package exposes a standalone WebSocket signaling listener. Version 1 exchanges one
complete SDP offer and answer, with ICE candidates gathered into SDP. Each signaling connection
owns one peer until close/disconnect. Applications select ICE servers, authorization, and native
track/data-channel handlers. No default STUN/TURN service or credentials are supplied.

## Consequences

The simple signaling state machine avoids trickle-ICE ordering and candidate buffering but can
increase setup latency. A TURN deployment is required for networks where direct ICE paths fail.
Multi-party SFU routing, room coordination, recording, and cross-replica state need separate designs.
