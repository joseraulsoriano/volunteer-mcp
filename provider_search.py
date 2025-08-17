#!/usr/bin/env python3
import os
import json
import time
import calendar
from datetime import datetime, timezone
import urllib.parse
from typing import Any, Dict, List, Optional
import asyncio

import aiohttp
from bs4 import BeautifulSoup
try:
    import redis  # type: ignore
except Exception:
    redis = None


class ProviderSearch:
    def __init__(self):
        self.brave_api_key: Optional[str] = os.getenv("BRAVE_API_KEY")
        # Rate limiting
        self.max_rps: float = float(os.getenv("BRAVE_MAX_RPS", "0.8"))  # < 1 req/seg por defecto
        self.monthly_quota: int = int(os.getenv("BRAVE_MONTHLY_QUOTA", "2000"))
        self._last_request_ts: float = 0.0
        self._rate_lock: Optional[asyncio.Lock] = None
        # Redis opcional para cuota mensual persistente
        self._redis = None
        url = os.getenv("REDIS_URL") or ""
        if url and redis is not None:
            try:
                self._redis = redis.Redis.from_url(url, decode_responses=True)
            except Exception:
                self._redis = None

    async def _search_brave(self, query: str, topK: int) -> Dict[str, Any]:
        if not self.brave_api_key:
            return {"results": []}
        # Cuotas: RPS y mensual
        if not await self._respect_rps():
            return {"results": []}
        if not await self._respect_monthly_quota():
            return {"results": []}
        url = f"https://api.search.brave.com/res/v1/web/search?q={urllib.parse.quote_plus(query)}&count={min(topK, 30)}"
        headers = {"X-Subscription-Token": self.brave_api_key}
        async with aiohttp.ClientSession(headers=headers) as session:
            try:
                async with session.get(url, timeout=12) as r:
                    if r.status != 200:
                        return {"results": []}
                    data = await r.json()
            except Exception:
                return {"results": []}
        results: List[Dict[str, Any]] = []
        for item in (data.get("web", {}).get("results") or [])[:topK]:
            results.append({
                "title": item.get("title"),
                "url": item.get("url"),
                "snippet": item.get("description"),
            })
        return {"results": results}

    async def _respect_rps(self) -> bool:
        # Asegura intervalo mínimo entre peticiones Brave
        if self.max_rps <= 0:
            return False
        min_interval = 1.0 / self.max_rps
        if self._rate_lock is None:
            import asyncio as _asyncio
            self._rate_lock = _asyncio.Lock()
        async with self._rate_lock:
            now = time.monotonic()
            wait_s = self._last_request_ts + min_interval - now
            if wait_s > 0:
                try:
                    import asyncio as _asyncio
                    await _asyncio.sleep(wait_s)
                except Exception:
                    pass
            self._last_request_ts = time.monotonic()
        return True

    async def _respect_monthly_quota(self) -> bool:
        # Limite mensual: 2000 por defecto
        if self.monthly_quota <= 0:
            return False
        # Con Redis: contador persistente por mes
        ym = datetime.now(timezone.utc).strftime("%Y-%m")
        key = f"brave:quota:{ym}"
        if self._redis is not None:
            try:
                # incrementa y fija expiración al fin de mes
                val = self._redis.incr(key)
                # expira a fin de mes
                now = datetime.now(timezone.utc)
                last_day = calendar.monthrange(now.year, now.month)[1]
                end_of_month = datetime(now.year, now.month, last_day, 23, 59, 59, tzinfo=timezone.utc)
                ttl = int((end_of_month - now).total_seconds())
                if ttl > 0:
                    self._redis.expire(key, ttl)
                if int(val) > self.monthly_quota:
                    return False
                return True
            except Exception:
                pass
        # Sin Redis: contador en memoria (se pierde al reiniciar)
        # Nota: simple fallback no-concurrente
        if not hasattr(self, "_monthly_counts"):
            setattr(self, "_monthly_counts", {})
        counts = getattr(self, "_monthly_counts")
        current = int(counts.get(ym, 0)) + 1
        counts[ym] = current
        if current > self.monthly_quota:
            return False
        return True

    async def _search_ddg_html(self, query: str, topK: int) -> Dict[str, Any]:
        # Usamos el mirror de r.jina.ai para evitar bloqueos de HTML pesado
        ddg = f"https://r.jina.ai/http://duckduckgo.com/html/?q={urllib.parse.quote_plus(query)}"
        async with aiohttp.ClientSession() as session:
            try:
                async with session.get(ddg, timeout=12) as r:
                    if r.status != 200:
                        return {"results": []}
                    text = await r.text()
            except Exception:
                return {"results": []}
        soup = BeautifulSoup(text, "lxml")
        results: List[Dict[str, Any]] = []
        for a in soup.select("a.result__a, a.result__url")[:topK]:
            title = a.get_text(strip=True)
            href = a.get("href")
            if not href:
                continue
            results.append({"title": title, "url": href, "snippet": ""})
        return {"results": results}

    async def search_boosted(
        self,
        query: str,
        topK: int = 10,
        domains: Optional[List[str]] = None,
        keywords: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        # Aumenta la consulta con dominios/keywords si se proveen
        parts = [query]
        if domains:
            parts.append("(" + " OR ".join(domains) + ")")
        if keywords:
            parts.append("(" + " OR ".join(keywords) + ")")
        boosted = " ".join(parts)

        # 1) Brave si hay API Key
        if self.brave_api_key:
            res = await self._search_brave(boosted, topK)
            res["boosted"] = boosted
            return res
        # 2) Fallback DDG HTML scraping ligero
        res = await self._search_ddg_html(boosted, topK)
        res["boosted"] = boosted
        return res


provider_search = ProviderSearch()


