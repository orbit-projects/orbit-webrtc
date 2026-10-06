from __future__ import annotations

import pytest

from orbit_webrtc import ICEConfiguration, WebRTCSettings
from orbit_webrtc.server import MAX_SIGNAL_BYTES, WebRTCServer


def test_ice_credentials_are_redacted() -> None:
    config = ICEConfiguration(("turn:turn.example:3478",), "ephemeral-user", "secret-value")
    assert "secret-value" not in repr(config)
    assert "ephemeral-user" not in repr(config)
    assert "credentials_configured=True" in repr(config)


@pytest.mark.parametrize(
    "kwargs",
    [
        {"port": True},
        {"path": "rtc"},
        {"origins": ()},
        {"max_peers": 0},
        {"max_data_channels": 257},
        {"max_sdp_bytes": MAX_SIGNAL_BYTES + 1},
        {"origins": ("https://example.test/path",)},
    ],
)
def test_settings_reject_unsafe_or_unbounded_values(kwargs: dict[str, object]) -> None:
    with pytest.raises((TypeError, ValueError)):
        WebRTCSettings(**kwargs)  # type: ignore[arg-type]


def test_ice_credentials_must_be_complete_and_urls_cannot_embed_secrets() -> None:
    with pytest.raises(ValueError):
        ICEConfiguration(("turn:turn.example:3478",), "username", None)
    with pytest.raises(ValueError):
        ICEConfiguration(("turn:user:password@turn.example:3478",))


def test_offer_parser_requires_exact_versioned_schema() -> None:
    server = WebRTCServer(WebRTCSettings())
    assert server._decode_offer('{"v":1,"type":"offer","sdp":"v=0"}') == "v=0"
    for invalid in (
        '{"v":2,"type":"offer","sdp":"v=0"}',
        '{"v":1,"type":"answer","sdp":"v=0"}',
        '{"v":1,"type":"offer","sdp":"v=0","extra":true}',
        "not-json",
    ):
        with pytest.raises(ValueError):
            server._decode_offer(invalid)
