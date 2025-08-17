#!/usr/bin/env python3
from fastapi import FastAPI, HTTPException, Response
from dotenv import load_dotenv
from fastapi.middleware.cors import CORSMiddleware
import uvicorn
from datetime import datetime
from typing import Dict, Any
from prometheus_client import generate_latest, CONTENT_TYPE_LATEST

# Cargar variables .env si existe localmente (en producción Railway se usan env vars)
load_dotenv()
from main import volunteer_mcp_server

app = FastAPI(title="Volunteer MCP", version="1.0.0")

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

    

if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=8011, log_level="info")

