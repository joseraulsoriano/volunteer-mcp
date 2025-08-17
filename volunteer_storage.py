#!/usr/bin/env python3
import os
import json
from typing import Any, Dict, List

try:
    import redis  # type: ignore
except Exception:
    redis = None


class VolunteerStorage:
    def __init__(self):
        self._mem_results: List[Dict[str, Any]] = []
        self._mem_alerts: List[Dict[str, Any]] = []
        self._redis = None
        url = os.getenv("REDIS_URL") or ""
        if url and redis is not None:
            try:
                self._redis = redis.Redis.from_url(url, decode_responses=True)
            except Exception:
                self._redis = None

    async def store_results(self, items: List[Dict[str, Any]]) -> Dict[str, Any]:
        if not items:
            return {"stored": 0}
        if self._redis:
            pipe = self._redis.pipeline()
            for it in items:
                pipe.lpush("vol:results", json.dumps(it, ensure_ascii=False))
            pipe.ltrim("vol:results", 0, 999)
            pipe.execute()
        else:
            self._mem_results.extend(items)
            if len(self._mem_results) > 1000:
                self._mem_results = self._mem_results[-1000:]
        return {"stored": len(items)}

    async def subscribe_alerts(self, profile: Dict[str, Any]) -> Dict[str, Any]:
        if not profile:
            return {"success": False, "error": "perfil vacío"}
        if self._redis:
            self._redis.lpush("vol:alerts", json.dumps(profile, ensure_ascii=False))
            self._redis.ltrim("vol:alerts", 0, 499)
        else:
            self._mem_alerts.append(profile)
            if len(self._mem_alerts) > 500:
                self._mem_alerts = self._mem_alerts[-500:]
        return {"success": True}

    async def get_alerts(self, params: Dict[str, Any]) -> Dict[str, Any]:
        limit = int(params.get("limit", 50))
        if self._redis:
            items = [json.loads(x) for x in self._redis.lrange("vol:alerts", 0, limit - 1)]
        else:
            items = self._mem_alerts[-limit:]
        return {"alerts": items, "count": len(items)}


volunteer_storage = VolunteerStorage()


