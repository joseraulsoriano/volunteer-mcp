#!/usr/bin/env python3
from fastapi import FastAPI, HTTPException, Response
from dotenv import load_dotenv
from fastapi.middleware.cors import CORSMiddleware
import uvicorn
from datetime import datetime
from typing import Dict, Any
import unicodedata
from prometheus_client import generate_latest, CONTENT_TYPE_LATEST
import os
import json
try:
    import redis  # type: ignore
except Exception:
    redis = None

# Cargar variables .env si existe localmente (en producción Railway se usan env vars)
load_dotenv()
from main import volunteer_mcp_server

app = FastAPI(title="Volunteer MCP", version="1.0.0")

# Redis opcional para blobs (persistencia entre deploys)
_redis = None
_redis_url = os.getenv("REDIS_URL") or ""
if _redis_url and redis is not None:
    try:
        _redis = redis.Redis.from_url(_redis_url, decode_responses=True)
    except Exception:
        _redis = None

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.get("/health")
async def health():
    return {"status": "healthy", "timestamp": datetime.now().isoformat()}

@app.get("/metrics")
async def metrics():
    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)

@app.get("/tools")
async def tools():
    return {"tools": volunteer_mcp_server.get_tools(), "count": len(volunteer_mcp_server.get_tools())}

@app.post("/mcp/call")
async def call(req: Dict[str, Any]):
    try:
        return await volunteer_mcp_server.handle_request(req)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/mcp/volunteer.prompt_search")
async def prompt_search(data: Dict[str, Any]):
    return await volunteer_mcp_server.handle_request({"tool": "volunteer.prompt_search", "params": data})

@app.post("/mcp/volunteer.search")
async def search(data: Dict[str, Any]):
    return await volunteer_mcp_server.handle_request({"tool": "volunteer.search", "params": data})

@app.post("/mcp/volunteer.rank")
async def rank(data: Dict[str, Any]):
    return await volunteer_mcp_server.handle_request({"tool": "volunteer.rank", "params": data})

@app.post("/mcp/volunteer.subscribe_alerts")
async def subscribe_alerts(data: Dict[str, Any]):
    return await volunteer_mcp_server.handle_request({"tool": "volunteer.subscribe_alerts", "params": data})

@app.post("/mcp/volunteer.get_alerts")
async def get_alerts(data: Dict[str, Any]):
    return await volunteer_mcp_server.handle_request({"tool": "volunteer.get_alerts", "params": data})

@app.post("/mcp/volunteer.collect")
async def collect(data: Dict[str, Any]):
    return await volunteer_mcp_server.handle_request({"tool": "volunteer.collect", "params": data})

@app.post("/mcp/volunteer.mx_collect")
async def mx_collect(data: Dict[str, Any]):
    return await volunteer_mcp_server.handle_request({"tool": "volunteer.mx_collect", "params": data})

@app.post("/mcp/volunteer.mx_search")
async def mx_search(data: Dict[str, Any]):
    return await volunteer_mcp_server.handle_request({"tool": "volunteer.mx_search", "params": data})

@app.post("/mcp/volunteer.career_search")
async def career_search(data: Dict[str, Any]):
    return await volunteer_mcp_server.handle_request({"tool": "volunteer.career_search", "params": data})

@app.post("/mcp/education.search")
async def education_search(data: Dict[str, Any]):
    return await volunteer_mcp_server.handle_request({"tool": "education.search", "params": data})

@app.post("/mcp/volunteer.area_search")
async def area_search(data: Dict[str, Any]):
    return await volunteer_mcp_server.handle_request({"tool": "volunteer.area_search", "params": data})

@app.post("/mcp/jobs.search")
async def jobs_search(data: Dict[str, Any]):
    return await volunteer_mcp_server.handle_request({"tool": "jobs.search", "params": data})

@app.post("/mcp/jobs.list")
async def jobs_list(data: Dict[str, Any]):
    return await volunteer_mcp_server.handle_request({"tool": "jobs.list", "params": data})

@app.post("/mcp/learning.plan.save")
async def learning_plan_save(data: Dict[str, Any]):
    return await volunteer_mcp_server.handle_request({"tool": "learning.plan.save", "params": data})

@app.post("/mcp/learning.plan.get")
async def learning_plan_get(data: Dict[str, Any]):
    return await volunteer_mcp_server.handle_request({"tool": "learning.plan.get", "params": data})

@app.get("/education/enriched")
async def education_enriched():
    try:
        # 1) Redis
        if _redis is not None:
            raw = _redis.get("edu:enriched")
            if raw:
                data = json.loads(raw)
                return {"success": True, "count": len(data) if isinstance(data, list) else 1, "data": data}
        # 2) Archivo
        path = "data/edu_enriched.json"
        if os.path.exists(path):
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            return {"success": True, "count": len(data) if isinstance(data, list) else 1, "data": data}
        if isinstance(data, list):
            return {"success": True, "count": len(data), "data": data}
        return {"success": True, "count": 0, "data": []}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/education/details")
async def education_details():
    try:
        # 1) Redis
        if _redis is not None:
            raw = _redis.get("edu:details")
            if raw:
                data = json.loads(raw)
                return {"success": True, "count": len(data) if isinstance(data, list) else 1, "data": data}
        # 2) Archivo
        path = "data/edu_details.json"
        if os.path.exists(path):
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            return {"success": True, "count": len(data) if isinstance(data, list) else 1, "data": data}
        return {"success": True, "count": 0, "data": []}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/education/all")
async def education_all():
    try:
        def _normalize_text(text: str) -> str:
            if not isinstance(text, str):
                text = str(text or "")
            text = text.strip().lower()
            text = unicodedata.normalize("NFD", text)
            text = "".join(ch for ch in text if unicodedata.category(ch) != "Mn")
            text = " ".join(text.split())
            return text

        def _key(name: str, state: str = "") -> str:
            return f"{_normalize_text(name)}|{_normalize_text(state)}"

        # Read sources from Redis or files
        enriched_data = None
        details_data = None

        if _redis is not None:
            try:
                raw_e = _redis.get("edu:enriched")
                if raw_e:
                    enriched_data = json.loads(raw_e)
            except Exception:
                enriched_data = None
            try:
                raw_d = _redis.get("edu:details")
                if raw_d:
                    details_data = json.loads(raw_d)
            except Exception:
                details_data = None

        if enriched_data is None:
            path_e = "data/edu_enriched.json"
            if os.path.exists(path_e):
                with open(path_e, "r", encoding="utf-8") as f:
                    enriched_data = json.load(f)
            else:
                enriched_data = []
        if details_data is None:
            path_d = "data/edu_details.json"
            if os.path.exists(path_d):
                with open(path_d, "r", encoding="utf-8") as f:
                    details_data = json.load(f)
            else:
                details_data = []

        # Build index by normalized name (details often lack state)
        details_index: Dict[str, Any] = {}
        for d in details_data or []:
            nombre = d.get("nombre") or d.get("name") or ""
            details_index[_key(nombre, "")] = d

        combined = []
        for e in enriched_data or []:
            state = e.get("state") or ""
            name = e.get("name") or e.get("nombre") or ""
            d = details_index.get(_key(name, state)) or details_index.get(_key(name, ""))
            merged = dict(e)
            if d:
                if "carreras" in d:
                    merged["carreras"] = d.get("carreras")
                if "costo" in d:
                    merged["costo"] = d.get("costo")
                if d.get("ubicacion"):
                    merged["ubicacion"] = d.get("ubicacion")
                merged["details_raw"] = d
            combined.append(merged)

        return {"success": True, "count": len(combined), "data": combined}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

def _career_soft_skills(career_name: str) -> set:
    """Mapea heurísticamente una carrera a soft skills relevantes."""
    n = (career_name or "").strip().lower()
    skills = set()
    # Habilidades generales
    if any(k in n for k in ["ingenier", "matem", "física", "fisica", "quim", "biolog", "sistemas", "comput"]):
        skills.update(["pensamiento analítico", "resolución de problemas", "trabajo en equipo", "aprendizaje continuo"])
    if any(k in n for k in ["arquitect", "diseñ", "diseno", "arte", "creativ"]):
        skills.update(["creatividad", "comunicación", "gestión del tiempo", "atención al detalle"])
    if any(k in n for k in ["psicolog", "educaci", "docencia", "pedagog"]):
        skills.update(["empatía", "comunicación", "escucha activa", "paciencia"])
    if any(k in n for k in ["derecho", "legal", "abog"]):
        skills.update(["comunicación", "negociación", "pensamiento crítico", "ética"])
    if any(k in n for k in ["medicina", "salud", "enfermer"]):
        skills.update(["empatía", "trabajo bajo presión", "trabajo en equipo", "ética"])
    if any(k in n for k in ["administraci", "gestión", "negocios", "empres"]):
        skills.update(["liderazgo", "planificación", "comunicación", "toma de decisiones"])
    if any(k in n for k in ["contadur", "finanz", "econom"]):
        skills.update(["atención al detalle", "pensamiento analítico", "ética", "gestión del tiempo"])
    if any(k in n for k in ["mercadotec", "marketing", "comercial", "publicidad"]):
        skills.update(["creatividad", "comunicación", "persuasión", "alfabetización de datos"])
    if any(k in n for k in ["relaciones internacionales", "relaciones", "internacional"]):
        skills.update(["negociación", "competencia intercultural", "comunicación", "resolución de conflictos"])
    if any(k in n for k in ["logística", "logistica", "cadenas", "operaciones"]):
        skills.update(["planificación", "organización", "comunicación", "resolución de problemas"])
    if not skills:
        # fallback genérico
        skills.update(["comunicación", "trabajo en equipo", "pensamiento crítico"])
    return skills

@app.get("/education/skills")
async def education_by_skills(skills: str = "", match: str = "all", state: str = "", tipo: str = "", limit: int = 50, offset: int = 0):
    """Filtra universidades por soft skills inferidas de sus carreras.

    Parámetros:
    - skills: CSV de habilidades (ej. "comunicación,trabajo en equipo")
    - match: "all" (todas) o "any" (cualquiera)
    - state: estado opcional
    - tipo: "publica" | "privada" opcional
    - limit, offset: paginación
    """
    try:
        # Reutilizar combinación de /education/all
        resp = await education_all()
        data = resp.get("data", [])

        # Normalizar filtros
        requested = [s.strip().lower() for s in skills.split(",") if s.strip()] if skills else []
        match_all = (match or "all").lower() != "any"

        filtered = []
        for item in data:
            if state and (item.get("state") or "").lower() != state.strip().lower():
                continue
            if tipo and (item.get("type") or "").lower() != tipo.strip().lower():
                continue
            carreras = item.get("carreras") or []
            inferred = set()
            for c in carreras:
                inferred.update(_career_soft_skills(str(c)))
            item_skills = sorted(inferred)
            # Guardar skills inferidas en el item de salida
            enriched_item = dict(item)
            enriched_item["skills"] = item_skills
            if requested:
                rs = set(requested)
                present = set(s.lower() for s in item_skills)
                ok = rs.issubset(present) if match_all else bool(rs & present)
                if not ok:
                    continue
            filtered.append(enriched_item)

        total = len(filtered)
        return {
            "success": True,
            "total": total,
            "limit": limit,
            "offset": offset,
            "data": filtered[offset: offset + limit]
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

    

if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=8011, log_level="info")

