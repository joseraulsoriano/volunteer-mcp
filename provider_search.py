#!/usr/bin/env python3
import os
import json
import urllib.parse
from typing import Any, Dict, List, Optional

import aiohttp
from bs4 import BeautifulSoup


class ProviderSearch:
    def __init__(self):
        self.brave_api_key: Optional[str] = os.getenv("BRAVE_API_KEY")

    async def _search_brave(self, query: str, topK: int) -> Dict[str, Any]:
        if not self.brave_api_key:
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


