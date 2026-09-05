"""Cache-aside for the two reads that are both expensive and safe to cache.

Only two things are cached here, chosen because caching them is provably
safe, not because caching is available:

- Waveform viewport windows (`ViewportCache.waveform_key`): a
  RecordingVersion is immutable once its database row exists (ADR 0002,
  ADR 0007) -- the same (version_id, channels, window, resolution) request
  can never legitimately return a different answer. There is no
  invalidation to get wrong, only a TTL to bound memory.
- QC summaries (`ViewportCache.qc_key`): same reasoning, keyed by the
  immutable version_id.

Recording *metadata* (state, active_version_id) is deliberately not cached
here: it mutates during ingestion/processing, caching it would require
explicit invalidation on every state transition, and reading it is already
a cheap indexed primary-key lookup -- caching it would add invalidation risk
for no measurable benefit.

Every operation fails open: a Redis outage or timeout is a cache miss, not
a request failure. Losing the cache must only cost latency, never
correctness or availability -- the same reliability posture as the outbox
dispatcher's broker-failure handling in services.jobs.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any

import redis
import structlog

from neuro_workspace.metrics import CACHE_REQUESTS_TOTAL

logger = structlog.get_logger(__name__)

WAVEFORM_TTL_SECONDS = 3600
QC_TTL_SECONDS = 3600
CACHE_VERSION = "v1"  # bump to invalidate everything after a payload-shape change


@dataclass(slots=True)
class CacheStats:
    hits: int = 0
    misses: int = 0
    errors: int = 0

    @property
    def hit_rate(self) -> float | None:
        total = self.hits + self.misses
        return None if total == 0 else self.hits / total


class ViewportCache:
    def __init__(self, redis_url: str) -> None:
        # Short timeouts are deliberate: a slow/unreachable Redis must fail
        # open quickly rather than making every request wait out a long
        # default socket timeout before falling back to computing fresh.
        self._client = redis.Redis.from_url(
            redis_url, decode_responses=True, socket_timeout=0.5, socket_connect_timeout=0.5
        )
        self.stats = CacheStats()

    def waveform_key(
        self,
        version_id: str,
        channel_indices: list[int],
        start_seconds: float,
        duration_seconds: float,
        pixel_width: int,
    ) -> str:
        payload = json.dumps(
            [version_id, sorted(channel_indices), start_seconds, duration_seconds, pixel_width]
        )
        digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()[:32]
        return f"waveform:{CACHE_VERSION}:{digest}"

    def qc_key(self, version_id: str) -> str:
        return f"qc:{CACHE_VERSION}:{version_id}"

    def get(self, key: str) -> dict[str, Any] | None:
        cache_name = key.split(":", 1)[0]
        try:
            raw = self._client.get(key)
        except redis.RedisError:
            self.stats.errors += 1
            CACHE_REQUESTS_TOTAL.labels(cache=cache_name, outcome="error").inc()
            logger.warning("cache_unavailable", key=key, op="get")
            return None
        if raw is None:
            self.stats.misses += 1
            CACHE_REQUESTS_TOTAL.labels(cache=cache_name, outcome="miss").inc()
            return None
        self.stats.hits += 1
        CACHE_REQUESTS_TOTAL.labels(cache=cache_name, outcome="hit").inc()
        # redis-py types Redis.get() as a sync/async union because the same
        # class backs both clients; this instance is always constructed
        # synchronous (Redis.from_url with no is_async), so the result here
        # is always a str, never an Awaitable.
        return dict(json.loads(str(raw)))

    def set(self, key: str, value: dict[str, Any], *, ttl_seconds: int) -> None:
        try:
            self._client.set(key, json.dumps(value), ex=ttl_seconds)
        except redis.RedisError:
            self.stats.errors += 1
            CACHE_REQUESTS_TOTAL.labels(cache=key.split(":", 1)[0], outcome="error").inc()
            logger.warning("cache_unavailable", key=key, op="set")

    def get_waveform(self, key: str) -> dict[str, Any] | None:
        return self.get(key)

    def set_waveform(self, key: str, value: dict[str, Any]) -> None:
        self.set(key, value, ttl_seconds=WAVEFORM_TTL_SECONDS)

    def get_qc(self, version_id: str) -> dict[str, Any] | None:
        return self.get(self.qc_key(version_id))

    def set_qc(self, version_id: str, value: dict[str, Any]) -> None:
        self.set(self.qc_key(version_id), value, ttl_seconds=QC_TTL_SECONDS)
