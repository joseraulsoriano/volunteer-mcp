#!/usr/bin/env python3
from typing import Any, Dict, List


class VolunteerRanker:
    async def rank(self, results: List[Dict[str, Any]], filters: Dict[str, Any]) -> List[Dict[str, Any]]:
        def score(item: Dict[str, Any]) -> float:
            # prioriza coincidencias simples por ubicación/carrera y señal explícita de score
            base = float(item.get("rank_score") or item.get("score") or 0.5)
            boost = 0.0
            location_filter = (filters or {}).get("location", "").lower()
            if location_filter and any(isinstance(loc, str) and location_filter in loc.lower() for loc in item.get("locations", [])):
                boost += 0.1
            career_filter = (filters or {}).get("field", "").lower()
            careers = [c.lower() for c in item.get("career", [])]
            if career_filter and career_filter in careers:
                boost += 0.1
            return base + boost

        ranked = sorted(results or [], key=score, reverse=True)
        # normaliza campo rank_score
        for it in ranked:
            if "rank_score" not in it:
                it["rank_score"] = float(it.get("score", 0.5))
        return ranked


volunteer_ranker = VolunteerRanker()


