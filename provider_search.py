#!/usr/bin/env python3
"""
Provider Search (Brave/Bing) with query booster, TTL cache, and optional SWR
"""

import os
import time
import asyncio
from typing import Dict, Any, List, Tuple
import aiohttp
from redis_cache import redis_cache


class TTLCache:
    def __init__(self, ttl_seconds: int = 1800, swr_seconds: int = 600):
        self.ttl = ttl_seconds
        self.swr = swr_seconds
        self.store: Dict[str, Tuple[float, Any]] = {}

    def get(self, key: str):
        entry = self.store.get(key)
        if not entry:
            return None, False, False
        ts, value = entry
        age = time.time() - ts
        fresh = age <= self.ttl
        swr_ok = age <= (self.ttl + self.swr)
        return value, fresh, swr_ok

    def set(self, key: str, value: Any):
        self.store[key] = (time.time(), value)


class ProviderSearch:
    def __init__(self):
        self.brave_key = os.getenv("BRAVE_API_KEY", "")
        self.cache = TTLCache(ttl_seconds=1800, swr_seconds=900)

    def _normalize_query(self, q: str) -> str:
        return " ".join(q.lower().split())

    def _boost_query(self, q: str, domains: List[str] | None = None, keywords: List[str] | None = None) -> str:
        domains = domains or [
            "site:.gob.mx",
            "site:.edu.mx",
            "site:unv.org",
            "site:idealist.org",
            "site:cruzrojamexicana.org.mx",
        ]
        keywords = keywords or [
            "voluntariado",
            "servicio social",
            "convocatoria",
            "vacante",
            "programa",
        ]
        booster = f"({ ' OR '.join(domains) }) ({ ' OR '.join(keywords) })"
        return f"{q} {booster}"

    async def _brave_search(self, query: str, topK: int = 5) -> List[Dict[str, Any]]:
        if not self.brave_key:
            return []
        url = "https://api.search.brave.com/res/v1/web/search"
        headers = {"X-Subscription-Token": self.brave_key}
        params = {"q": query, "count": max(1, min(10, topK))}
        async with aiohttp.ClientSession() as session:
            try:
                async with session.get(url, headers=headers, params=params, timeout=1.5) as r:
                    if r.status != 200:
                        return []
                    data = await r.json()
                    results = []
                    for item in (data.get("web", {}).get("results", []) or [])[:topK]:
                        results.append({
                            "title": item.get("title"),
                            "url": item.get("url"),
                            "snippet": item.get("description"),
                            "source": "brave"
                        })
                    return results
            except Exception:
                return []

    async def search_boosted(self, query: str, topK: int = 5, domains: List[str] | None = None, keywords: List[str] | None = None) -> Dict[str, Any]:
        qn = self._normalize_query(query)
        boosted = self._boost_query(qn, domains=domains, keywords=keywords)
        key = f"brave:{boosted}:{topK}"

        # Try Redis first
        rcached, rfresh, rswr = redis_cache.get_swr(key)
        if rfresh and rcached is not None:
            return {"from_cache": True, "results": rcached}

        # Fallback to in-memory cache
        cached, fresh, swr_ok = self.cache.get(key)
        if fresh and cached is not None:
            return {"from_cache": True, "results": cached}

        async def refresh():
            results = await self._brave_search(boosted, topK)
            if results:
                # set both redis and memory
                redis_cache.set_swr(key, results, ttl_seconds=1800, swr_seconds=900)
                self.cache.set(key, results)
            return results

        # Serve stale from Redis first
        if rswr and rcached is not None:
            asyncio.create_task(refresh())
            return {"from_cache": True, "results": rcached}

        # Or serve stale from memory
        if swr_ok and cached is not None:
            asyncio.create_task(refresh())
            return {"from_cache": True, "results": cached}

        results = await refresh()
        return {"from_cache": False, "results": results}


provider_search = ProviderSearch()


