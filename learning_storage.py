#!/usr/bin/env python3
import os
import json
from typing import Any, Dict, Optional

try:
    import redis  # type: ignore
except Exception:
    redis = None


class LearningStorage:
    def __init__(self):
        self._mem: Dict[str, Dict[str, Any]] = {}
        self._redis = None
        url = os.getenv("REDIS_URL") or ""
        if url and redis is not None:
            try:
                self._redis = redis.Redis.from_url(url, decode_responses=True)
            except Exception:
                self._redis = None

    def _key(self, profile_id: str) -> str:
        return f"learning:plan:{profile_id}"

    def save_plan(self, profile_id: str, plan: Dict[str, Any]) -> Dict[str, Any]:
        if not profile_id or not isinstance(plan, dict):
            return {"success": False, "error": "parámetros inválidos"}
        if self._redis:
            self._redis.set(self._key(profile_id), json.dumps(plan, ensure_ascii=False))
        else:
            self._mem[profile_id] = plan
        return {"success": True}

    def get_plan(self, profile_id: str) -> Dict[str, Any]:
        if not profile_id:
            return {"success": False, "error": "profile_id requerido"}
        if self._redis:
            raw = self._redis.get(self._key(profile_id))
            if raw:
                try:
                    return {"success": True, "plan": json.loads(raw)}
                except Exception:
                    return {"success": False, "error": "plan corrupto"}
            return {"success": True, "plan": None}
        return {"success": True, "plan": self._mem.get(profile_id)}


learning_storage = LearningStorage()


