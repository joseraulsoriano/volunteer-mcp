#!/usr/bin/env python3
import os
import re
import json
import asyncio
from typing import Any, Dict, List, Optional, Tuple

from provider_search import provider_search


def read_universities(input_path: str) -> List[Dict[str, Any]]:
    with open(input_path, "r", encoding="utf-8") as f:
        data = json.load(f)
    universities: List[Dict[str, Any]] = []
    if isinstance(data, dict):
        for state, items in data.items():
            for it in items:
                universities.append({
                    "state": state,
                    "name": it.get("name"),
                    "type": it.get("type"),
                    "position": it.get("position", {}),
                })
    elif isinstance(data, list):
        universities = data
    return universities


def domain_from_url(url: str) -> str:
    try:
        from urllib.parse import urlparse
        netloc = urlparse(url).netloc.lower()
        return netloc
    except Exception:
        return ""


def tokens_from_name(name: str) -> List[str]:
    txt = (name or "").lower()
    txt = re.sub(r"[^a-záéíóúñü0-9 ]+", " ", txt)
    parts = [p for p in txt.split() if len(p) >= 3]
    return parts


def is_probably_official(url: str, uni_name: str) -> bool:
    d = domain_from_url(url)
    if not d:
        return False
    # señales de oficialidad
    if d.endswith(".edu.mx"):
        return True
    name_tokens = tokens_from_name(uni_name)
    if any(t in d for t in name_tokens[:3]):
        return True
    if any(k in d for k in ["unam", "ipn", "uam", "udg", "tec.mx", "anahuac", "ibero", "up.edu.mx", "uvm.mx", "udlap.mx"]):
        return True
    return False


def pick_best_link(results: List[Dict[str, Any]], uni_name: str, include_keywords: List[str]) -> Optional[str]:
    # priorizar enlaces oficiales que contengan ciertas palabras
    scored: List[Tuple[int, str]] = []
    for it in results or []:
        url = it.get("url") or ""
        if not url:
            continue
        score = 0
        if is_probably_official(url, uni_name):
            score += 5
        txt = (it.get("title") or "") + " " + (it.get("snippet") or "") + " " + url
        tl = txt.lower()
        for kw in include_keywords:
            if kw in tl:
                score += 2
        # penalizar agregadores
        if any(k in domain_from_url(url) for k in ["cursosycarreras", "educaedu", "emagister", "computrabajo", "occ.com.mx"]):
            score -= 3
        scored.append((score, url))
    scored.sort(key=lambda x: x[0], reverse=True)
    return scored[0][1] if scored else None


async def search_category(uni: Dict[str, Any], what: str, topK: int = 10) -> Optional[str]:
    """what in {tuition, careers, curricula} -> returns a URL if found"""
    name = uni.get("name") or ""
    state = uni.get("state") or ""
    if what == "tuition":
        query = f"{name} {state} colegiaturas costos colegiatura aranceles"
        include = ["colegiatura", "colegiaturas", "costo", "costos", "arancel"]
    elif what == "careers":
        query = f"{name} {state} licenciaturas carreras oferta académica"
        include = ["licenciatura", "licenciaturas", "carreras", "oferta"]
    else:  # curricula
        query = f"{name} {state} plan de estudios mapa curricular malla"
        include = ["plan de estudios", "mapa curricular", "malla"]

    res = await provider_search.search_boosted(query, topK)
    url = pick_best_link(res.get("results") or [], name, include)
    return url


async def enrich_university(uni: Dict[str, Any]) -> Dict[str, Any]:
    tuition = await search_category(uni, "tuition")
    careers = await search_category(uni, "careers")
    curricula = await search_category(uni, "curricula")

    costs = {"inscripcion_mxn": None, "colegiatura_mxn": None, "periodicidad": None, "nota": None}
    # Nota: parsing de montos es frágil; solo extraemos la URL por ahora
    sources: List[str] = []
    for u in [tuition, careers, curricula]:
        if u and u not in sources:
            sources.append(u)

    return {
        "state": uni.get("state"),
        "name": uni.get("name"),
        "type": uni.get("type"),
        "position": uni.get("position", {}),
        "programas": [
            {"nivel": "licenciatura", "area": None, "nombre": None, "program_url": careers, "curricula_url": curricula}
        ] if (careers or curricula) else [],
        "costos": {**costs, "nota": "Ver URL de colegiaturas" if tuition else None},
        "tuition_url": tuition,
        "careers_url": careers,
        "curricula_url": curricula,
        "sources": sources,
    }


async def main_async(input_path: str, out_path: str, max_unis: int) -> int:
    unis = read_universities(input_path)
    # Toma las primeras max_unis (o menos si no hay tantas)
    selected = unis[: max_unis]
    results: List[Dict[str, Any]] = []
    for uni in selected:
        try:
            enriched = await enrich_university(uni)
            results.append(enriched)
        except Exception:
            continue
    os.makedirs(os.path.dirname(out_path) or ".", exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=2)
    print(out_path)
    return 0


def main() -> None:
    import argparse
    p = argparse.ArgumentParser(description="Enriquece universidades con URLs de costos/oferta/plan de estudios")
    p.add_argument("--in", dest="input_path", default="universidades_mx.json")
    p.add_argument("--out", dest="out_path", default="data/edu_enriched.json")
    p.add_argument("--max", dest="max_unis", type=int, default=10)
    args = p.parse_args()
    code = asyncio.run(main_async(args.input_path, args.out_path, args.max_unis))
    raise SystemExit(code)


if __name__ == "__main__":
    main()


