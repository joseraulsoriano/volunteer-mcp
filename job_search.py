#!/usr/bin/env python3
"""
Job Search MCP - Aggregador de ofertas laborales (headlines + enriquecido ligero)
Campos: title, link, images, organization, location, area, career, snippet, source, posted_at
"""

import re
import json
import asyncio
from datetime import datetime
from typing import Dict, Any, List

import aiohttp
from bs4 import BeautifulSoup

from provider_search import provider_search
from redis_cache import redis_cache


class JobSearch:
    def __init__(self):
        # Dominios confiables o comunes para empleo
        self.safe_domains = [
            "site:computrabajo.com.mx",
            "site:occ.com.mx",
            "site:indeed.com",
            "site:linkedin.com/jobs",
            "site:glassdoor.com",
            "site:ziprecruiter.com",
            "site:talent.com",
            "site:lever.co",
            "site:greenhouse.io",
            "site:smartrecruiters.com",
            "site:getonbrd.com",
            "site:jobs.lever.co",
            "site:boards.greenhouse.io",
        ]
        self.keywords = ["empleo", "oferta", "vacante", "trabajo", "remoto", "full time", "medio tiempo"]

    def _boost_query(self, query: str, area: str = "", career: str = "", location: str = "") -> str:
        parts = [query]
        if area:
            parts.append(area)
        if career:
            parts.append(career)
        if location:
            parts.append(location)
        parts.append("(" + " OR ".join(self.safe_domains) + ")")
        parts.append("(" + " OR ".join(self.keywords) + ")")
        return " ".join(parts)

    async def _fetch(self, url: str, timeout: int = 12) -> str:
        async with aiohttp.ClientSession() as session:
            try:
                async with session.get(url, timeout=timeout, headers={"User-Agent": "AgentsForLife/1.0"}) as r:
                    if r.status != 200:
                        return ""
                    return await r.text()
            except Exception:
                return ""

    def _extract_images(self, soup: BeautifulSoup) -> List[str]:
        images: List[str] = []
        og = soup.find("meta", attrs={"property": "og:image"})
        if og and og.get("content"):
            images.append(og.get("content"))
        for img in soup.select("img[src]")[:5]:
            src = img.get("src")
            if src and src not in images:
                images.append(src)
        return images[:5]

    def _extract_org(self, soup: BeautifulSoup) -> str:
        ogsite = soup.find("meta", attrs={"property": "og:site_name"})
        if ogsite and ogsite.get("content"):
            return ogsite.get("content")
        # JSON-LD Organization
        for script in soup.find_all("script", type="application/ld+json"):
            try:
                data = json.loads(script.text)
                if isinstance(data, dict):
                    if data.get("@type") in ["JobPosting", "Organization"]:
                        org = data.get("hiringOrganization") or data.get("name")
                        if isinstance(org, dict):
                            return org.get("name", "")
                        if isinstance(org, str):
                            return org
                if isinstance(data, list):
                    for d in data:
                        if isinstance(d, dict) and d.get("@type") in ["JobPosting", "Organization"]:
                            org = d.get("hiringOrganization") or d.get("name")
                            if isinstance(org, dict):
                                return org.get("name", "")
                            if isinstance(org, str):
                                return org
            except Exception:
                continue
        return ""

    def _extract_location(self, soup: BeautifulSoup, text: str) -> str:
        # JSON-LD JobPosting
        for script in soup.find_all("script", type="application/ld+json"):
            try:
                data = json.loads(script.text)
                if isinstance(data, dict) and data.get("@type") == "JobPosting":
                    loc = data.get("jobLocation")
                    if isinstance(loc, dict):
                        addr = loc.get("address") or {}
                        city = addr.get("addressLocality")
                        region = addr.get("addressRegion")
                        country = addr.get("addressCountry")
                        parts = [p for p in [city, region, country] if p]
                        if parts:
                            return ", ".join(parts)
            except Exception:
                continue
        # fallback simple por regex
        m = re.search(r"(CDMX|Ciudad de M[eé]xico|Guadalajara|Monterrey|Puebla|Quer[eé]taro|Remoto)", text, re.I)
        return m.group(0) if m else ""

    def _infer_area_career(self, title: str, snippet: str) -> Dict[str, str]:
        t = (title + " " + snippet).lower()
        area = ""
        career = ""
        if any(k in t for k in ["logist", "supply", "cadena de suministro", "almacén"]):
            area = area or "logística"
            career = career or "logística"
        if any(k in t for k in ["agric", "agro", "campo", "agronom"]):
            area = area or "ambiental"
            career = career or "agricultura"
        if any(k in t for k in ["salud", "enfermer", "m[eé]dic"]):
            area = area or "salud"
        if any(k in t for k in ["educ", "docent", "mentor"]):
            area = area or "educación"
        if any(k in t for k in ["impacto", "comunitario", "social", "ong"]):
            area = area or "social"
        return {"area": area or "", "career": career or ""}

    async def search(self, params: Dict[str, Any]) -> Dict[str, Any]:
        query = params.get("query", "oferta de trabajo")
        topK = int(params.get("topK", 30))
        area = params.get("area", "")
        career = params.get("career", "")
        location = params.get("location", "")

        key = f"jobs.search:{json.dumps({"q": query, "k": topK, "a": area, "c": career, "l": location}, sort_keys=True, ensure_ascii=False)}"
        cached, fresh, _ = redis_cache.get_swr(key)
        if fresh and cached is not None:
            return {"success": True, **cached}

        boosted = self._boost_query(query, area, career, location)
        provider = await provider_search.search_boosted(
            boosted,
            topK,
            domains=self.safe_domains,
            keywords=self.keywords,
        )
        headlines = provider.get("results", [])

        # Enriquecer 1–3 URLs top en paralelo
        enriched: List[Dict[str, Any]] = []
        for item in headlines[: min(3, len(headlines))]:
            url = item.get("url")
            if not url:
                continue
            html = await self._fetch(url)
            if not html:
                continue
            soup = BeautifulSoup(html, "lxml")
            title = item.get("title") or (soup.title.string.strip() if soup.title and soup.title.string else "")
            snippet = item.get("snippet") or ""
            images = self._extract_images(soup)
            org = self._extract_org(soup)
            loc = self._extract_location(soup, soup.get_text(" ")[:1000])
            infer = self._infer_area_career(title or "", snippet)
            enriched.append({
                "title": title,
                "link": url,
                "images": images,
                "organization": org,
                "location": loc or location,
                "area": infer["area"] or area,
                "career": infer["career"] or career,
                "snippet": snippet,
                "source": "provider",
                "posted_at": datetime.now().isoformat(),
            })

        result = {
            "query": query,
            "boosted": boosted,
            "headlines": headlines,
            "enriched": enriched,
            "count": len(headlines),
            "timestamp": datetime.now().isoformat(),
        }
        # SWR: TTL 30 min, SWR 10 min
        redis_cache.set_swr(key, result, ttl_seconds=1800, swr_seconds=600)
        return {"success": True, **result}


job_search = JobSearch()


