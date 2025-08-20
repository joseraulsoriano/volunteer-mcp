### Volunteer MCP
FastAPI MCP (Model Context Protocol) para búsqueda y ranking de voluntariados y educación, con enfoque México.

## Características
- **API HTTP con FastAPI** y CORS abierto
- **Búsqueda de voluntariados (MX)**: Worldpackers + resultados tipo noticias/listados (vía Brave)
- **Deduplicación y enlaces reales**: URL canónica y `apply_link` cuando exista
- **Datos educativos (MX)**: enriquecimiento de universidades y extracción de carreras/costos
- **MCP tools** expuestas vía endpoints `/mcp/*`
- **Prometheus metrics** en `/metrics`
- **Redis opcional** para caché y blobs; **SQLite/Postgres** para empleos

## Requisitos
- Python 3.11+
- Opcional: Redis (para caché y blobs)
- Opcional: Postgres (si usas `DATABASE_URL`), de lo contrario SQLite local
- Opcional: Brave Search API (`BRAVE_API_KEY`) para mejores resultados

## Configuración rápida (local)
```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

# Variables de entorno (opcional)
export BRAVE_API_KEY=tu_api_key   # mejora búsquedas
export REDIS_URL=redis://...      # cache/blobs
export DATABASE_URL=postgres://...# jobs storage; si no, SQLite local

# Iniciar servidor
uvicorn mcp_api:app --reload --host 127.0.0.1 --port 8011
```

## Endpoints principales
- Salud/observabilidad
  - `GET /health`
  - `GET /metrics` (Prometheus)
  - `GET /tools` (lista de herramientas MCP disponibles)

- MCP (POST JSON, ver ejemplos más abajo)
  - `/mcp/volunteer.prompt_search`
  - `/mcp/volunteer.search`
  - `/mcp/volunteer.rank`
  - `/mcp/volunteer.subscribe_alerts`
  - `/mcp/volunteer.get_alerts`
  - `/mcp/volunteer.collect`
  - `/mcp/volunteer.mx_collect`
  - `/mcp/volunteer.mx_search`
  - `/mcp/volunteer.career_search`
  - `/mcp/volunteer.area_search`
  - `/mcp/education.search`
  - `/mcp/jobs.search`
  - `/mcp/jobs.list`
  - `/mcp/learning.plan.save`
  - `/mcp/learning.plan.get`

- Educación (GET)
  - `GET /education/enriched` → lee `data/edu_enriched.json` o Redis `edu:enriched`
  - `GET /education/details` → lee `data/edu_details.json` o Redis `edu:details`
  - `GET /education/all` → une enriched + details on-the-fly
  - `GET /education/skills?skills=...&match=any|all&state=...&tipo=...` → filtra por soft skills inferidas

## Utilidades CLI (datos educativos)
- Enriquecer con URLs (colegiaturas, carreras, currículo):
```bash
python education_enricher.py --in universidades_mx.json --out data/edu_enriched.json --max 100000
```
- Extraer detalles (carreras, costo):
```bash
python education_details.py --in data/edu_enriched.json --out data/edu_details.json --max 100000
```
- Combinar ambos en uno:
```bash
python education_combine.py --enriched data/edu_enriched.json --details data/edu_details.json --out data/edu_all.json
```

## Búsqueda de voluntariados (comportamiento)
- Enfoque MX: `collect_mexico` usa solo fuentes MX seguras:
  - Worldpackers (posiciones), con extracción de detalles + `apply_link`
  - Noticias/listados vía Brave (dominios gob.mx, Cruz Roja, TECHO, Worldpackers, voluntariado.net, UNV)
- Deduplicación por URL canónica y fusión de ítems duplicados (combina imágenes, conserva mejor score)
- Enlaces “reales”: se prioriza `apply_link` cuando existe (acción), sin perder `link/source`

## Ejemplos (curl)
```bash
# Voluntariados México (resultados normalizados)
curl -s -X POST http://127.0.0.1:8011/mcp/volunteer.mx_search \
  -H 'content-type: application/json' \
  -d '{"filters": {"location": "cdmx"}}' | jq '.count, .results[:2]'

# Educación: dataset combinado
curl -s http://127.0.0.1:8011/education/all | jq '.count, .data[:1]'

# Educación por soft skills
curl -s "http://127.0.0.1:8011/education/skills?skills=comunicacion,trabajo%20en%20equipo&match=all" | jq '.total'

# Jobs
curl -s -X POST http://127.0.0.1:8011/mcp/jobs.search -H 'content-type: application/json' -d '{"query":"data analyst mexico"}' | jq '.count, .saved_jobs'
```

## Variables de entorno
- **BRAVE_API_KEY**: clave para Brave Search API (mejores resultados)
- **BRAVE_MAX_RPS**: límite de solicitudes por segundo (default `0.8`)
- **BRAVE_MONTHLY_QUOTA**: cuota mensual (default `2000`)
- **REDIS_URL**: URL de Redis (caché SWR y blobs `edu:*`, `vol:*`)
- **DATABASE_URL**: conexión a Postgres; si no se especifica, se usa `sqlite:///data/education_jobs.db`

## Persistencia y métricas
- Redis (opcional):
  - Caché SWR para búsquedas (`vol.search:*`, `vol.mx:*`, etc.)
  - Blobs educativos (`edu:enriched`, `edu:details`)
- Base de datos (SQLite/Postgres) para `jobs` vía SQLAlchemy
- Prometheus en `GET /metrics`

## Despliegue (Railway)
- `Procfile`:
```bash
web: uvicorn mcp_api:app --host 0.0.0.0 --port ${PORT:-8000}
```
- Asegura configurar variables de entorno en Railway (especialmente `BRAVE_API_KEY` y `REDIS_URL` si aplica)

## Estructura principal
- `mcp_api.py`: FastAPI app y endpoints
- `main.py`: implementación de herramientas MCP (server de herramientas)
- `provider_search.py`: integración Brave/DDG, rate limits y cuotas
- `volunteer_search.py`: fuentes, normalización MX, dedupe/canonicalización, `apply_link`
- `volunteer_ranker.py`: ranking simple por score y filtros
- `education_enricher.py`: URLs de colegiaturas/carreras/currículo
- `education_details.py`: extracción de carreras/costos desde HTML
- `education_combine.py`: combinación de enriched + details
- `learning_storage.py`, `volunteer_storage.py`, `education_storage.py`: persistencia
- `job_search.py`: búsqueda de empleos (y persistencia de resultados)

## Notas
- La extracción de educación es heurística y dependiente del HTML; valida resultados
- Brave puede bloquearse si se exceden cuotas; revisa `BRAVE_MAX_RPS` y `BRAVE_MONTHLY_QUOTA`
- Para datos completos de educación, ejecuta los scripts CLI con `--max` alto antes de consumir los endpoints

