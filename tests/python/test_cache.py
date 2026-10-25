"""ViewportCache correctness: deterministic keys, real hit/miss behavior
against a Redis-protocol-compatible in-memory server (fakeredis, not a
mock), and fail-open behavior when Redis is unreachable.
"""

from __future__ import annotations

import fakeredis
import pytest
from fastapi.testclient import TestClient

from neuro_workspace.cache import ViewportCache
from test_api_integration import create_workspace_project, upload_csv


@pytest.fixture
def cache() -> ViewportCache:
    instance = ViewportCache("redis://localhost:6379/0")
    instance._client = fakeredis.FakeRedis(decode_responses=True)
    return instance


def test_waveform_key_is_deterministic_and_order_independent_for_channels(
    cache: ViewportCache,
) -> None:
    key_a = cache.waveform_key("v1", [0, 1, 2], 0.0, 5.0, 800)
    key_b = cache.waveform_key("v1", [2, 1, 0], 0.0, 5.0, 800)
    assert key_a == key_b  # channel order must not fragment the cache


def test_waveform_key_differs_for_different_versions_or_windows(cache: ViewportCache) -> None:
    base = cache.waveform_key("v1", [0, 1], 0.0, 5.0, 800)
    assert cache.waveform_key("v2", [0, 1], 0.0, 5.0, 800) != base
    assert cache.waveform_key("v1", [0, 1], 1.0, 5.0, 800) != base
    assert cache.waveform_key("v1", [0, 1], 0.0, 5.0, 400) != base


def test_get_after_set_is_a_hit_with_identical_content(cache: ViewportCache) -> None:
    key = cache.waveform_key("v1", [0], 0.0, 1.0, 100)
    assert cache.get(key) is None
    assert cache.stats.misses == 1

    payload = {"start_seconds": 0.0, "channels": [1, 2, 3]}
    cache.set(key, payload, ttl_seconds=60)

    assert cache.get(key) == payload
    assert cache.stats.hits == 1


def test_fails_open_when_redis_is_unreachable() -> None:
    # Point at a port nothing listens on; the short connect timeout in
    # ViewportCache.__init__ keeps this fast rather than hanging.
    cache = ViewportCache("redis://localhost:1/0")
    key = cache.waveform_key("v1", [0], 0.0, 1.0, 100)

    result = cache.get(key)  # must not raise
    assert result is None
    assert cache.stats.errors == 1

    cache.set(key, {"x": 1}, ttl_seconds=60)  # must not raise
    assert cache.stats.errors == 2


def test_waveform_endpoint_returns_etag_and_304s_on_if_none_match(
    signed_in_client: TestClient,
) -> None:
    client = signed_in_client
    _, project_id = create_workspace_project(client)
    recording_id, _ = upload_csv(client, project_id)

    params = {
        "start_seconds": 0,
        "duration_seconds": 1,
        "channel_index": [0, 1],
        "pixel_width": 80,
    }
    first = client.get(f"/api/recordings/{recording_id}/waveform", params=params)
    assert first.status_code == 200, first.text
    etag = first.headers.get("etag")
    assert etag

    second = client.get(
        f"/api/recordings/{recording_id}/waveform",
        params=params,
        headers={"If-None-Match": etag},
    )
    assert second.status_code == 304
    assert second.content == b""


def test_waveform_cache_never_outlives_the_version_it_was_rendered_from(
    signed_in_client: TestClient,
) -> None:
    """Processing switches a recording's active version under the same URL, so
    an immutable response is only safe when the request pins the version.
    """

    client = signed_in_client
    _, project_id = create_workspace_project(client)
    recording_id, _ = upload_csv(client, project_id)
    url = f"/api/recordings/{recording_id}/waveform"
    params = {"start_seconds": 0, "duration_seconds": 1, "channel_index": [0, 1], "pixel_width": 80}
    original_version = client.get(f"/api/recordings/{recording_id}").json()["active_version_id"]

    unpinned = client.get(url, params=params)
    assert unpinned.headers["cache-control"] == "private, no-cache"
    pinned = client.get(url, params={**params, "version_id": original_version})
    assert "immutable" in pinned.headers["cache-control"]

    processing = client.post(
        f"/api/recordings/{recording_id}/processing-runs",
        json={"operation_id": "average_reference", "parameters": {"channel_indices": [0, 1]}},
    )
    assert processing.status_code == 202, processing.text
    processed_version = client.get(f"/api/recordings/{recording_id}").json()["active_version_id"]
    assert processed_version != original_version

    stale_pin = client.get(url, params={**params, "version_id": original_version})
    assert stale_pin.status_code == 409, stale_pin.text
    assert stale_pin.json()["error"]["code"] == "version_changed"

    revalidated = client.get(
        url, params=params, headers={"If-None-Match": unpinned.headers["etag"]}
    )
    assert revalidated.status_code == 200
    assert revalidated.headers["etag"] != unpinned.headers["etag"]
