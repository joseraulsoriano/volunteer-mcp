#!/usr/bin/env python3
from prometheus_client import Counter, Histogram

# Métricas básicas para observabilidad
MCP_REQUESTS_TOTAL = Counter(
    "mcp_requests_total",
    "Total de solicitudes MCP",
    labelnames=["tool"],
)

MCP_ERRORS_TOTAL = Counter(
    "mcp_errors_total",
    "Total de errores MCP",
    labelnames=["tool"],
)

MCP_TOOL_DURATION_MS = Histogram(
    "mcp_tool_duration_ms",
    "Duración de cada herramienta MCP en milisegundos",
    labelnames=["tool"],
    buckets=[5, 10, 25, 50, 100, 250, 500, 1000, 2500, 5000, 10000],
)


