#!/usr/bin/env python3
"""
Volunteer MCP - Agents for Life
MCP especializado para búsqueda y ranking de voluntariados
"""

import asyncio
import json
import logging
import time
from datetime import datetime
from typing import Dict, Any, List

from volunteer_search import volunteer_search
from provider_search import provider_search
from metrics import MCP_REQUESTS_TOTAL, MCP_ERRORS_TOTAL, MCP_TOOL_DURATION_MS
from job_search import job_search
from volunteer_ranker import volunteer_ranker
from volunteer_storage import volunteer_storage
from education_storage import save_jobs

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


class VolunteerMCPServer:
    def __init__(self):
        self.tools = {
            "volunteer.prompt_search": self._prompt_search,
            "volunteer.search": self._search,
            "volunteer.rank": self._rank,
            "volunteer.subscribe_alerts": self._subscribe_alerts,
            "volunteer.get_alerts": self._get_alerts,
            "volunteer.collect": self._collect,
            "volunteer.mx_collect": self._mx_collect,
            "volunteer.mx_search": self._mx_search,
            "volunteer.career_search": self._career_search,
            "volunteer.area_search": self._area_search,
            "education.search": self._education_search,
            "jobs.search": self._jobs_search,
            "jobs.list": self._jobs_list,
        }
        self.stats = {
            "requests": 0,
            "errors": 0,
            "start_time": datetime.now().isoformat(),
            "tool_metrics": {}
        }

    def get_tools(self) -> List[str]:
        return list(self.tools.keys())

    async def handle_request(self, request: Dict[str, Any]) -> Dict[str, Any]:
        try:
            self.stats["requests"] += 1
            tool = request.get("tool", "")
            params = request.get("params", {})

            if tool not in self.tools:
                return {"success": False, "error": f"Tool not found: {tool}", "available_tools": self.get_tools()}

            start = time.perf_counter()
            result = await self.tools[tool](params)
            duration_ms = (time.perf_counter() - start) * 1000.0

            # metrics
            MCP_REQUESTS_TOTAL.labels(tool=tool).inc()
            MCP_TOOL_DURATION_MS.labels(tool=tool).observe(duration_ms)

            tm = self.stats["tool_metrics"].setdefault(tool, {"calls": 0, "total_ms": 0.0, "avg_ms": 0.0, "last_ms": 0.0})
            tm["calls"] += 1
            tm["total_ms"] += duration_ms
            tm["last_ms"] = duration_ms
            tm["avg_ms"] = tm["total_ms"] / max(1, tm["calls"]) 

            return {
                "success": True,
                "result": result,
                "tool": tool,
                "duration_ms": round(duration_ms, 2),
                "timestamp": datetime.now().isoformat(),
            }
        except Exception as e:
            self.stats["errors"] += 1
            MCP_ERRORS_TOTAL.labels(tool=request.get("tool", "unknown")).inc()
            logger.error(f"Error: {e}")
            return {"success": False, "error": str(e)}

    async def _prompt_search(self, params: Dict[str, Any]) -> Dict[str, Any]:
        """Convertir prompt libre a filtros y ejecutar búsqueda"""
        prompt = params.get("prompt", "")
        default_location = params.get("location", "")
        # Parsing heurístico simple (puede reemplazarse por LLM)
        filters = volunteer_search.parse_prompt(prompt, default_location)
        results = await volunteer_search.search(filters)
        ranked = await volunteer_ranker.rank(results, filters)
        await volunteer_storage.store_results(ranked)
        return {"filters": filters, "results": ranked, "count": len(ranked)}

    async def _search(self, params: Dict[str, Any]) -> Dict[str, Any]:
        filters = params.get("filters", {})
        results = await volunteer_search.search(filters)
        return {"filters": filters, "results": results, "count": len(results)}

    async def _rank(self, params: Dict[str, Any]) -> Dict[str, Any]:
        results = params.get("results", [])
        filters = params.get("filters", {})
        ranked = await volunteer_ranker.rank(results, filters)
        return {"results": ranked, "count": len(ranked)}

    async def _subscribe_alerts(self, params: Dict[str, Any]) -> Dict[str, Any]:
        profile = params.get("profile", {})
        return await volunteer_storage.subscribe_alerts(profile)

    async def _get_alerts(self, params: Dict[str, Any]) -> Dict[str, Any]:
        return await volunteer_storage.get_alerts(params)

    async def _collect(self, params: Dict[str, Any]) -> Dict[str, Any]:
        """Recolección masiva desde todas las fuentes, almacena y devuelve conteo"""
        filters = params.get("filters", {})
        results = await volunteer_search.search(filters)
        ranked = await volunteer_ranker.rank(results, filters)
        await volunteer_storage.store_results(ranked)
        return {
            "success": True,
            "stored": len(ranked),
            "filters": filters,
            "sample": ranked[:5]
        }

    async def _mx_collect(self, params: Dict[str, Any]) -> Dict[str, Any]:
        filters = params.get("filters", {})
        mx = await volunteer_search.collect_mexico(filters)
        # guardamos también normalizados (mapea a tabla results con campos clave)
        await volunteer_storage.store_results([
            {
                "org": r.get("org"),
                "role": r.get("title"),
                "location": ", ".join(r.get("locations", [])),
                "need": ", ".join(r.get("career", [])),
                "hours": r.get("availability"),
                "score": r.get("rank_score", 0.6),
                "rank_score": r.get("rank_score", 0.6),
                "source": r.get("source"),
                "posted_at": r.get("posted_at"),
            } for r in mx
        ])
        return {"success": True, "stored": len(mx), "results": mx[:10]}

    async def _mx_search(self, params: Dict[str, Any]) -> Dict[str, Any]:
        filters = params.get("filters", {})
        mx = await volunteer_search.collect_mexico(filters)
        return {"success": True, "results": mx, "count": len(mx)}

    async def _career_search(self, params: Dict[str, Any]) -> Dict[str, Any]:
        careers = [c.lower() for c in params.get("careers", [])] or ["agricultura", "logística"]
        location = params.get("location", "")
        min_per = int(params.get("min_per", 10))
        safe_only = bool(params.get("safe_only", True))
        results = await volunteer_search.career_collect(careers, location, min_per, safe_only)
        return {"success": True, "by_career": results}

    async def _education_search(self, params: Dict[str, Any]) -> Dict[str, Any]:
        query = params.get("query", "voluntariado universitario cdmx")
        topK = int(params.get("topK", 5))
        res = await provider_search.search_boosted(query, topK)
        return {"success": True, **res}

    async def _area_search(self, params: Dict[str, Any]) -> Dict[str, Any]:
        areas = [a.lower() for a in params.get("areas", [])] or ["salud", "educación", "ambiental", "social", "logística"]
        location = params.get("location", "")
        min_per = int(params.get("min_per", 10))
        safe_only = bool(params.get("safe_only", True))
        results = await volunteer_search.area_collect(areas, location, min_per, safe_only)
        return {"success": True, "by_area": results}

    async def _jobs_search(self, params: Dict[str, Any]) -> Dict[str, Any]:
        res = await job_search.search(params)
        # Persistir headlines y enriched como postings
        items = []
        items.extend(res.get("headlines", []))
        items.extend(res.get("enriched", []))
        saved = save_jobs(items)
        res["saved_jobs"] = saved
        # Fallback: si no hay empleos, buscar voluntariados relacionados y devolver links
        if int(res.get("count", 0)) <= 0:
            query = params.get("query", "")
            location = params.get("location", "")
            try:
                filters = volunteer_search.parse_prompt(query, location)
            except Exception:
                filters = {"location": location} if location else {}
            try:
                vol_results = await volunteer_search.search(filters)
                ranked = await volunteer_ranker.rank(vol_results, filters)
            except Exception:
                ranked = []
            res["fallback_type"] = "volunteer"
            res["fallback"] = {
                "filters": filters,
                "count": len(ranked),
                "results": ranked[:10]
            }
        return res

    async def _jobs_list(self, params: Dict[str, Any]) -> Dict[str, Any]:
        q = params.get("q")
        location = params.get("location")
        area = params.get("area")
        career = params.get("career")
        limit = int(params.get("limit", 20))
        offset = int(params.get("offset", 0))
        data = list_jobs(q=q, location=location, area=area, career=career, limit=limit, offset=offset)
        return {"success": True, **data}

    


volunteer_mcp_server = VolunteerMCPServer()


if __name__ == "__main__":
    print("🚀 Volunteer MCP Server")
    for t in volunteer_mcp_server.get_tools():
        print(" -", t)

