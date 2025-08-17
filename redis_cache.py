#!/usr/bin/env python3
import os
import json
import time
from typing import Any, Dict, Optional, Tuple

try:
    import redis  # type: ignore
except Exception:
    redis = None  # graceful fallback


class _InMemorySWR:
    def __init__(self):
        self._store: Dict[str, Tuple[float, float, Any]] = {}

    def get_swr(self, key: str) -> Tuple[Optional[Any], bool, bool]:
        now = time.time()
        if key not in self._store:
            return None, False, False
        exp, swr_until, value = self._store[key]
        fresh = now < exp
        swr_ok = now < swr_until
        return value, fresh, swr_ok

    def set_swr(self, key: str, value: Any, ttl_seconds: int = 1800, swr_seconds: int = 600) -> None:
        now = time.time()
        self._store[key] = (now + ttl_seconds, now + ttl_seconds + swr_seconds, value)

    def append_archive(self, list_key: str, item: Dict[str, Any]) -> None:
        # mantener último N=200
        key = f"archive:{list_key}"
        cached, _, _ = self.get_swr(key)
        arr = cached or []
        arr.append(item)
        if len(arr) > 200:
            arr = arr[-200:]
        # 24h de TTL para archivo
        self.set_swr(key, arr, ttl_seconds=86400, swr_seconds=0)


class _RedisSWR:
    def __init__(self, url: str):
        assert redis is not None, "redis package not installed"
        self.client = redis.Redis.from_url(url, decode_responses=True)

    def get_swr(self, key: str) -> Tuple[Optional[Any], bool, bool]:
        raw = self.client.get(key)
        if raw is None:
            return None, False, False
        try:
            data = json.loads(raw)
        except Exception:
            return None, False, False
        value = data.get("value")
        exp = float(data.get("exp", 0))
        swr_until = float(data.get("swr", 0))
        now = time.time()
        fresh = now < exp
        swr_ok = now < swr_until
        return value, fresh, swr_ok

    def set_swr(self, key: str, value: Any, ttl_seconds: int = 1800, swr_seconds: int = 600) -> None:
        now = time.time()
        payload = {
            "value": value,
            "exp": now + ttl_seconds,
            "swr": now + ttl_seconds + swr_seconds,
        }
        # establecemos expiración real en exp+swr
        ex = ttl_seconds + swr_seconds
        self.client.set(key, json.dumps(payload, ensure_ascii=False), ex=ex)

    def append_archive(self, list_key: str, item: Dict[str, Any]) -> None:
        key = f"archive:{list_key}"
        self.client.lpush(key, json.dumps(item, ensure_ascii=False))
        self.client.ltrim(key, 0, 199)
        # 24h TTL para archivo
        self.client.expire(key, 86400)


class RedisCacheFacade:
    def __init__(self):
        url = os.getenv("REDIS_URL") or os.getenv("UPSTASH_REDIS_REST_URL") or ""
        if url and redis is not None:
            try:
                self.backend = _RedisSWR(url)
            except Exception:
                self.backend = _InMemorySWR()
        else:
            self.backend = _InMemorySWR()

    def get_swr(self, key: str):
        return self.backend.get_swr(key)

    def set_swr(self, key: str, value: Any, ttl_seconds: int = 1800, swr_seconds: int = 600) -> None:
        self.backend.set_swr(key, value, ttl_seconds=ttl_seconds, swr_seconds=swr_seconds)

    def append_archive(self, list_key: str, item: Dict[str, Any]) -> None:
        self.backend.append_archive(list_key, item)


redis_cache = RedisCacheFacade()


