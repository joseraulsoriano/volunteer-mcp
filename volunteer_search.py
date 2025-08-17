#!/usr/bin/env python3
import re
import logging
from datetime import datetime
from typing import Dict, Any, List, Optional, Set

import aiohttp
from bs4 import BeautifulSoup
import asyncio
import json
from redis_cache import redis_cache
from provider_search import provider_search

logger = logging.getLogger(__name__)


class VolunteerSearch:
    """Búsqueda en múltiples fuentes públicas (simples)"""
    def __init__(self):
        self.sources = [
            self._source_gob_mx_voluntariado,
            self._source_gob_mx_indesol,
            self._source_cruz_roja_mx,
            self._source_techo_mx,
            self._source_un_online_volunteering,
            self._source_workaway_ngo,
            self._source_worldpackers,
            self._source_ayuda_en_accion,
            self._source_voluntariado_net
        ]

        self.mx_locations = [
            "méxico", "mexico", "mx", "cdmx", "ciudad de méxico", "monterrey", "guadalajara",
            "puebla", "querétaro", "tijuana", "león", "merida", "mérida", "cancún", "toluca",
            "san luis", "morelia", "cuernavaca", "hermosillo", "chihuahua", "veracruz", "tampico",
            "acapulco", "tuxtla", "colima", "zacatecas", "saltillo", "culiacán", "culiacan",
            "jalapa", "xalapa", "coatzacoalcos", "villahermosa"
        ]
        self.safe_sources = [
            "https://www.gob.mx",
            "https://www.onlinevolunteering.org",
            "http://www.unv.org",
            "https://ayudaenaccion.org.mx/voluntariado/",
            "https://worldpackers.com",
            "https://www.workaway.info",
            "https://voluntariado.net",
            "https://cruzrojamexicana.org.mx",
            "https://techo.org"
        ]

    def parse_prompt(self, prompt: str, default_location: str = "") -> Dict[str, Any]:
        text = prompt.lower()
        filters = {
            "location": default_location or self._extract_location(text),
            "field": self._extract_field(text),
            "need": self._extract_need(text),
            "availability": self._extract_availability(text),
        }
        return {k: v for k, v in filters.items() if v}

    async def search(self, filters: Dict[str, Any]) -> List[Dict[str, Any]]:
        # Redis SWR cache
        key = f"vol.search:{json.dumps(filters, sort_keys=True, ensure_ascii=False)}"
        cached, fresh, swr_ok = redis_cache.get_swr(key)
        if fresh and cached is not None:
            return cached
        results: List[Dict[str, Any]] = []
        tasks = [src(filters) for src in self.sources]
        for coro in asyncio.as_completed(tasks):
            try:
                items = await coro
                results.extend(items)
            except Exception as e:
                logger.debug(f"source error: {e}")
        # Save cache (30 min TTL, 10 min SWR)
        redis_cache.set_swr(key, results, ttl_seconds=1800, swr_seconds=600)
        return results

    # -------- México (filtros y normalización) --------
    def _is_mexico_item(self, item: Dict[str, Any]) -> bool:
        text = " ".join([str(item.get(k, "")) for k in ["org", "role", "location", "need", "hours", "source", "link"]]).lower()
        return any(loc in text for loc in self.mx_locations)

    def _infer_career(self, item: Dict[str, Any]) -> List[str]:
        text = (str(item.get("role", "")) + " " + str(item.get("need", ""))).lower()
        mapping = {
            "salud": ["salud", "enfermer", "médic", "medic"],
            "educación": ["educ", "docente", "mentor", "mentoría"],
            "ambiental": ["ambient", "reforest", "clima", "tortuga", "conserv"],
            "social": ["social", "comunit", "banco de alimentos", "alimentos"],
            "legal": ["legal", "derecho"],
            "ti": ["ti ", "software", "datos", "data", "sistema"],
            "logística": ["logíst", "logist", "cadena de suministro", "suministro"],
            "agricultura": ["agric", "agro", "campo", "granja", "huerto", "siembra", "cosecha", "alimentos"],
        }
        careers = []
        for k, kws in mapping.items():
            if any(kw in text for kw in kws):
                careers.append(k)
        return careers or ["general"]

    def _extract_salary(self, item: Dict[str, Any]) -> str:
        # Si ya viene un salario/beneficios explícito, úsalo
        salary_explicit = str(item.get("salary", "")).strip()
        if salary_explicit:
            return salary_explicit
        for field in ["role", "need", "details"]:
            txt = str(item.get(field, ""))
            if "$" in txt or "MXN" in txt or "USD" in txt:
                return txt
        return "No remunerado / N/A"

    def _extract_images_from_html(self, html: str) -> List[str]:
        try:
            soup = BeautifulSoup(html, "lxml")
            images: List[str] = []
            og = soup.find("meta", attrs={"property": "og:image"})
            if og and og.get("content"):
                images.append(og.get("content"))
            for img in soup.select("img[src]")[:5]:
                src = img.get("src")
                if src and src not in images:
                    images.append(src)
            return images[:5]
        except Exception:
            return []

    def _extract_worldpackers_position_details(self, html: str, url: str) -> Dict[str, Any]:
        """Extrae detalles enriquecidos desde una página de posición de Worldpackers.
        Intenta leer JSON-LD, h1, anclas a hosts, ubicación y beneficios/imagenes.
        """
        try:
            sp = BeautifulSoup(html, "lxml")
        except Exception:
            return {}

        # 1) JSON-LD
        ld_blocks: List[Dict[str, Any]] = []
        for s in sp.find_all("script", attrs={"type": "application/ld+json"}):
            try:
                data = json.loads(s.string or "{}")
                if isinstance(data, list):
                    ld_blocks.extend([d for d in data if isinstance(d, dict)])
                elif isinstance(data, dict):
                    ld_blocks.append(data)
            except Exception:
                continue

        # helpers para leer campos de ld+json
        def first_in(obj: Any, *keys: str) -> Optional[Any]:
            if not isinstance(obj, dict):
                return None
            for k in keys:
                if k in obj and obj[k]:
                    return obj[k]
            return None

        title: Optional[str] = None
        org: Optional[str] = None
        location_parts: List[str] = []
        images: List[str] = []
        salary_or_benefits: Optional[str] = None

        for block in ld_blocks:
            if not title:
                t = first_in(block, "title", "name")
                if isinstance(t, str) and t.strip():
                    title = t.strip()
            # organización
            if not org:
                org_obj = first_in(block, "hiringOrganization", "organization")
                if isinstance(org_obj, dict):
                    name = first_in(org_obj, "name")
                    if isinstance(name, str) and name.strip():
                        org = name.strip()
            # ubicación
            jl = first_in(block, "jobLocation", "address", "location")
            if isinstance(jl, dict):
                for k in ["addressLocality", "addressRegion", "addressCountry", "address" ]:
                    val = jl.get(k)
                    if isinstance(val, str) and val.strip():
                        location_parts.append(val.strip())
                address_obj = jl.get("address") if isinstance(jl.get("address"), dict) else None
                if isinstance(address_obj, dict):
                    for k in ["addressLocality", "addressRegion", "addressCountry"]:
                        val = address_obj.get(k)
                        if isinstance(val, str) and val.strip():
                            location_parts.append(val.strip())
            # imágenes
            img = first_in(block, "image", "thumbnailUrl")
            if isinstance(img, str) and img.strip():
                images.append(img.strip())
            elif isinstance(img, list):
                images.extend([i for i in img if isinstance(i, str)])
            # ofertas/salario
            offers = first_in(block, "offers")
            if isinstance(offers, dict):
                sal = first_in(offers, "salary", "baseSalary", "price")
                if isinstance(sal, (str, int, float)) and str(sal).strip():
                    salary_or_benefits = f"Salario/Apoyo: {sal}"

        # 2) Fallbacks desde el DOM
        if not title:
            h1 = sp.find("h1")
            if h1 and h1.get_text(strip=True):
                title = h1.get_text(strip=True)
        if not org:
            # buscar host/organización
            a_host = sp.select_one("a[href*='/host'], a[href*='/hosts/'], a[href*='/es/host']")
            if a_host and a_host.get_text(strip=True):
                org = a_host.get_text(strip=True)
        # ubicación heurística
        page_text = sp.get_text(" ", strip=True)
        if not location_parts:
            # heurística: busca país/ciudad en el texto o la URL
            loc_candidates: List[str] = []
            for loc in self.mx_locations:
                if re.search(r"(?i)\b" + re.escape(loc) + r"\b", page_text) or re.search(r"(?i)/" + re.escape(loc) + r"\b", url):
                    loc_candidates.append(loc)
            if loc_candidates:
                # formatea capitalizando primera letra
                location_parts = list(dict.fromkeys([loc.title() for loc in loc_candidates]))

        # imágenes OG + <img>
        og = sp.find("meta", attrs={"property": "og:image"})
        if og and og.get("content"):
            images.insert(0, og.get("content"))
        for img_tag in sp.select("img[src]")[:8]:
            src = img_tag.get("src")
            if src and src not in images:
                images.append(src)
        images = images[:8]

        # beneficios básicos (no remunerado pero con perks)
        if not salary_or_benefits:
            benefit_keywords = [
                "alojamiento", "comidas", "desayuno", "almuerzo", "cena",
                "transporte", "tours", "descuentos", "clases", "habitaci",
            ]
            found = sorted({
                kw for kw in benefit_keywords if re.search(r"(?i)\b" + kw, page_text)
            })
            if found:
                salary_or_benefits = "No remunerado / Beneficios: " + ", ".join(found)

        # enlace de aplicación directo si aparece
        apply_link: Optional[str] = None
        for a in sp.select("a[href]"):
            href = a.get("href") or ""
            txt = a.get_text(strip=True).lower()
            if any(x in href for x in ["apply", "postular", "candidatar", "aplicar"]) or any(x in txt for x in ["aplica", "postula", "apply"]):
                apply_link = href if href.startswith("http") else (f"https://worldpackers.com{href}" if href.startswith("/") else url)
                break

        # armar resultado
        details = ""
        if salary_or_benefits and salary_or_benefits.startswith("No remunerado"):
            details = salary_or_benefits

        return {
            "org": org or "Worldpackers",
            "role": title or "Voluntariado",
            "location": ", ".join(location_parts) if location_parts else ("México" if "/mexico" in url else "Global"),
            "need": "voluntariado",
            "hours": "variable",
            "score": 0.56,
            "source": url,
            "link": url,
            "images": images,
            "details": details,
            "salary": salary_or_benefits or "No remunerado / N/A",
            "apply_link": apply_link,
            "posted_at": datetime.now().isoformat(),
        }

    async def _fetch_positions_from_worldpackers(self, search_url: str, max_items: int = 12) -> List[Dict[str, Any]]:
        """Abrir una página de búsqueda de Worldpackers y extraer posiciones '/positions/*'."""
        html = await self._fetch_html(search_url, timeout=15)
        if not html:
            return []
        soup = BeautifulSoup(html, "lxml")
        # Recolectar enlaces únicos a posiciones
        anchors = soup.select("a[href*='/positions/'], a[href*='/es/positions/']")
        seen: Set[str] = set()
        position_links: List[str] = []
        for a in anchors:
            href = a.get("href")
            if not href:
                continue
            full = f"https://worldpackers.com{href}" if href.startswith("/") else href
            if "/positions/" not in full:
                continue
            if full in seen:
                continue
            seen.add(full)
            position_links.append(full)
            if len(position_links) >= max_items:
                break

        # Fetch detalle por posición (título, organización, ubicación, beneficios, imágenes)
        results: List[Dict[str, Any]] = []
        async def fetch_pos(url: str) -> Optional[Dict[str, Any]]:
            htmlp = await self._fetch_html(url, timeout=15)
            if not htmlp:
                return None
            details = self._extract_worldpackers_position_details(htmlp, url)
            if not details:
                # Fallback ultra simple
                sp = BeautifulSoup(htmlp, "lxml")
                title = (sp.title.string.strip() if sp.title and sp.title.string else "Voluntariado")
                imgs = self._extract_images_from_html(htmlp)
                details = {
                    "org": "Worldpackers",
                    "role": title,
                    "location": "Global",
                    "need": "voluntariado",
                    "hours": "variable",
                    "score": 0.56,
                    "source": url,
                    "link": url,
                    "images": imgs,
                    "salary": "No remunerado / N/A",
                    "posted_at": datetime.now().isoformat()
                }
            return details

        tasks = [fetch_pos(u) for u in position_links]
        for coro in asyncio.as_completed(tasks):
            try:
                item = await coro
                if item:
                    results.append(item)
            except Exception:
                continue
        return results

    async def _source_un_online_volunteering(self, filters: Dict[str, Any]) -> List[Dict[str, Any]]:
        """Extractor simple para UN Online Volunteering enfocado en IT/Software/Data."""
        base = "https://www.onlinevolunteering.org"
        # Páginas de categorías o búsqueda general (IT/Software/Data)
        candidates = [
            f"{base}/es/opportunities?search=software",
            f"{base}/es/opportunities?search=it",
            f"{base}/es/opportunities?search=data",
            f"{base}/es/opportunities?search=engineering",
        ]
        results: List[Dict[str, Any]] = []
        seen: Set[str] = set()
        for url in candidates:
            html = await self._fetch_html(url, timeout=15)
            if not html:
                continue
            soup = BeautifulSoup(html, "lxml")
            # enlaces a detalle (heurístico)
            for a in soup.select("a[href*='/es/']")[:50]:
                href = a.get("href") or ""
                if "/opportunity/" not in href and "/opportunities/" not in href:
                    continue
                full = href if href.startswith("http") else f"{base}{href}"
                if full in seen:
                    continue
                seen.add(full)
                title = a.get_text(strip=True) or "Voluntariado en línea"
                results.append({
                    "org": "UN Online Volunteering",
                    "role": title,
                    "location": "remoto",
                    "need": "it/software/data",
                    "hours": "variable",
                    "score": 0.6,
                    "source": url,
                    "link": full,
                    "images": [],
                    "posted_at": datetime.now().isoformat()
                })
            if len(results) >= 20:
                break
        return results

    def _normalize_mx(self, items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        normalized: List[Dict[str, Any]] = []
        for it in items:
            if not self._is_mexico_item(it):
                continue
            title = it.get("role") or it.get("org") or "Voluntariado"
            link = it.get("link") or it.get("source")
            loc = it.get("location", "México")
            career = self._infer_career(it)
            salary = self._extract_salary(it)
            details = it.get("need", "")
            normalized.append({
                "title": title,
                "position": title,
                "org": it.get("org", ""),
                "link": link,
                "locations": [loc] if isinstance(loc, str) else (loc or ["México"]),
                "career": career,
                "availability": it.get("hours", "variable"),
                "salary": salary,
                "details": details,
                "source": it.get("source"),
                "images": it.get("images", []),
                "apply_link": it.get("apply_link"),
                "posted_at": it.get("posted_at", datetime.now().isoformat()),
                "rank_score": it.get("rank_score", it.get("score", 0.5)),
            })
        return normalized

    def _is_safe(self, item: Dict[str, Any]) -> bool:
        src = (item.get("source") or "")
        link = (item.get("link") or "")
        return any(safe in src or safe in link for safe in self.safe_sources)

    async def collect_mexico(self, filters: Dict[str, Any]) -> List[Dict[str, Any]]:
        # ignoramos filtros de ubicación en la captura, traemos todo y filtramos por México localmente
        raw = await self.search({})
        mx_norm = self._normalize_mx(raw)
        # aplicar filtro de ubicación puntual si viene
        location = (filters or {}).get("location", "").lower()
        if location:
            mx_norm = [r for r in mx_norm if any(location in loc.lower() for loc in r.get("locations", []))]
        # Cache per-MX filter
        key = f"vol.mx:{json.dumps(filters, sort_keys=True, ensure_ascii=False)}"
        redis_cache.set_swr(key, mx_norm, ttl_seconds=1800, swr_seconds=600)
        return mx_norm

    async def career_collect(self, careers: List[str], location: str = "", min_per: int = 10, safe_only: bool = True) -> Dict[str, List[Dict[str, Any]]]:
        cache_key = f"vol.career:{json.dumps(sorted(careers), ensure_ascii=False)}:{location}:{min_per}:{safe_only}"
        cached, fresh, swr_ok = redis_cache.get_swr(cache_key)
        if fresh and cached is not None:
            return cached
        # 1) México primero
        mx = await self.collect_mexico({"location": location} if location else {})
        by_career: Dict[str, List[Dict[str, Any]]] = {c: [] for c in careers}

        def matches_career(item, c):
            return c in [x.lower() for x in item.get("career", [])]

        for c in careers:
            items = [i for i in mx if matches_career(i, c)]
            if safe_only:
                items = [i for i in items if self._is_safe(i)]
            by_career[c] = items[:min_per]

        # 2) Fallback global para completar min_per
        need_more = any(len(by_career[c]) < min_per for c in careers)
        if need_more:
            raw_global = await self.search({})
            # normalización global simple reutilizando normalizador MX pero sin filtrar por MX
            glob_norm: List[Dict[str, Any]] = []
            for it in raw_global:
                title = it.get("role") or it.get("org") or "Voluntariado"
                link = it.get("link") or it.get("source")
                loc = it.get("location", "Global")
                career = self._infer_career(it)
                salary = self._extract_salary(it)
                details = it.get("need", "")
                norm = {
                    "title": title,
                    "org": it.get("org", ""),
                    "link": link,
                    "locations": [loc] if isinstance(loc, str) else (loc or ["Global"]),
                    "career": career,
                    "availability": it.get("hours", "variable"),
                    "salary": salary,
                    "details": details,
                    "source": it.get("source"),
                    "posted_at": it.get("posted_at", datetime.now().isoformat()),
                    "rank_score": it.get("rank_score", it.get("score", 0.5)),
                }
                glob_norm.append(norm)

            for c in careers:
                if len(by_career[c]) >= min_per:
                    continue
                needed = min_per - len(by_career[c])
                extra = [i for i in glob_norm if matches_career(i, c)]
                if safe_only:
                    extra = [i for i in extra if self._is_safe(i)]
                # preferir los que incluyan MX si hay
                if location:
                    extra = sorted(extra, key=lambda x: any(location.lower() in loc.lower() for loc in x.get("locations", [])), reverse=True)
                by_career[c].extend(extra[:needed])

        # Save and archive sample
        redis_cache.set_swr(cache_key, by_career, ttl_seconds=1800, swr_seconds=600)
        redis_cache.append_archive("vol.archive", {"careers": careers, "location": location, "count": {k: len(v) for k, v in by_career.items()}, "ts": datetime.now().isoformat()})
        return by_career

    async def area_collect(self, areas: List[str], location: str = "", min_per: int = 10, safe_only: bool = True) -> Dict[str, List[Dict[str, Any]]]:
        """Agrupa por áreas MX y utiliza career_collect internamente. Devuelve items normalizados por área."""
        # Mapeo de áreas nacionales a carreras aproximadas
        area_to_careers = {
            "salud": ["salud"],
            "educación": ["educación"],
            "ambiental": ["ambiental", "agricultura"],
            "social": ["social", "legal"],
            "logística": ["logística"],
            # tech/"ciencias exactas" relacionadas
            "ti": ["ti", "sistemas", "ingeniería"],
            "sistemas": ["sistemas", "ti", "ingeniería"],
        }

        normalized_areas = [a.lower() for a in areas]
        result: Dict[str, List[Dict[str, Any]]] = {a: [] for a in normalized_areas}

        for area in normalized_areas:
            careers = area_to_careers.get(area, [area])
            by_career = await self.career_collect(careers, location, min_per, safe_only)

            # Flatten por área, dedupe por link
            collected: List[Dict[str, Any]] = []
            for items in by_career.values():
                collected.extend(items)

            seen_links: Set[str] = set()
            deduped: List[Dict[str, Any]] = []
            for item in collected:
                link = item.get("link") or item.get("source")
                if not link or link in seen_links:
                    continue
                seen_links.add(link)
                deduped.append(item)

            result[area] = deduped[:min_per]

        return result

    # Extractors (heurísticos simples)
    def _extract_location(self, text: str) -> str:
        for kw in ["mexico", "cdmx", "guadalajara", "monterrey", "spain", "madrid", "usa", "mexico city"]:
            if kw in text:
                return kw
        return ""

    def _extract_field(self, text: str) -> str:
        for kw in ["salud", "health", "educación", "education", "ti", "ingeniería", "law", "legal"]:
            if kw in text:
                return kw
        return ""

    def _extract_need(self, text: str) -> str:
        for kw in ["urgente", "emergencia", "crisis", "niños", "migrantes", "desastres"]:
            if kw in text:
                return kw
        return ""

    def _extract_availability(self, text: str) -> str:
        for kw in ["fin de semana", "noches", "medio tiempo", "full time", "remoto"]:
            if kw in text:
                return kw
        return ""

    async def _fetch_html(self, url: str, timeout: int = 15) -> str:
        headers = {
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_6) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36 AgentsForLife/1.0",
            "Accept-Language": "es-MX,es;q=0.9,en;q=0.8",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Connection": "keep-alive",
        }
        async with aiohttp.ClientSession(headers=headers) as session:
            try:
                async with session.get(url, timeout=timeout) as r:
                    if r.status != 200:
                        return ""
                    return await r.text()
            except Exception:
                return ""

    # Sources
    async def _source_gob_mx_voluntariado(self, filters: Dict[str, Any]) -> List[Dict[str, Any]]:
        # Página informativa; devolvemos ligas útiles (UNV, Voluntariado en línea)
        url = "https://www.gob.mx/sre/acciones-y-programas/voluntariado"
        html = await self._fetch_html(url)
        items: List[Dict[str, Any]] = []
        if html:
            items.append({
                "org": "Programa de Voluntarios de las Naciones Unidas",
                "role": "Voluntariado en línea / internacional",
                "location": "global",
                "need": "diverso",
                "hours": "remoto/presencial",
                "score": 0.8,
                "source": url,
                "link": "https://www.onlinevolunteering.org/es",
                "images": self._extract_images_from_html(html),
                "posted_at": datetime.now().isoformat()
            })
        return items

    async def _source_gob_mx_indesol(self, filters: Dict[str, Any]) -> List[Dict[str, Any]]:
        url = "https://www.gob.mx/indesol"
        html = await self._fetch_html(url)
        results: List[Dict[str, Any]] = []
        if not html:
            return results
        try:
            soup = BeautifulSoup(html, "lxml")
            links = soup.select("a[href]")
            for a in links[:200]:
                txt = (a.get_text(strip=True) or "").lower()
                href = a.get("href")
                if not href:
                    continue
                if any(k in txt for k in ["volunt", "servicio social", "convocatoria", "participa"]):
                    results.append({
                        "org": "INDESOL / Gobierno de México",
                        "role": a.get_text(strip=True) or "Voluntariado / Convocatoria",
                        "location": "México",
                        "need": "gobierno / social",
                        "hours": "variable",
                        "score": 0.62,
                        "source": url,
                        "link": href if href.startswith("http") else f"https://www.gob.mx{href}",
                        "images": self._extract_images_from_html(html),
                        "posted_at": datetime.now().isoformat()
                    })
        except Exception as e:
            logger.debug(f"indesol parse error: {e}")
        return results

    async def _source_cruz_roja_mx(self, filters: Dict[str, Any]) -> List[Dict[str, Any]]:
        url = "https://cruzrojamexicana.org.mx/"
        html = await self._fetch_html(url)
        results: List[Dict[str, Any]] = []
        if not html:
            return results
        try:
            soup = BeautifulSoup(html, "lxml")
            links = soup.select("a[href]")
            for a in links[:200]:
                txt = (a.get_text(strip=True) or "").lower()
                href = a.get("href")
                if not href:
                    continue
                if any(k in txt for k in ["volunt", "servicio social", "unete", "únete"]):
                    results.append({
                        "org": "Cruz Roja Mexicana",
                        "role": a.get_text(strip=True) or "Voluntariado",
                        "location": "México",
                        "need": "salud / emergencias",
                        "hours": "variable",
                        "score": 0.7,
                        "source": url,
                        "link": href if href.startswith("http") else f"https://cruzrojamexicana.org.mx/{href.lstrip('/')}",
                        "images": self._extract_images_from_html(html),
                        "posted_at": datetime.now().isoformat()
                    })
        except Exception as e:
            logger.debug(f"cruz roja parse error: {e}")
        return results

    async def _source_techo_mx(self, filters: Dict[str, Any]) -> List[Dict[str, Any]]:
        url = "https://techo.org/mexico"
        html = await self._fetch_html(url)
        results: List[Dict[str, Any]] = []
        if not html:
            return results
        try:
            soup = BeautifulSoup(html, "lxml")
            links = soup.select("a[href]")
            for a in links[:200]:
                txt = (a.get_text(strip=True) or "").lower()
                href = a.get("href")
                if not href:
                    continue
                if "volunt" in txt or "participa" in txt or "unete" in txt or "únete" in txt:
                    results.append({
                        "org": "TECHO México",
                        "role": a.get_text(strip=True) or "Voluntariado",
                        "location": "México",
                        "need": "social / comunitario",
                        "hours": "variable",
                        "score": 0.66,
                        "source": url,
                        "link": href if href.startswith("http") else (f"https://techo.org{href}" if href.startswith("/") else href),
                        "images": self._extract_images_from_html(html),
                        "posted_at": datetime.now().isoformat()
                    })
        except Exception as e:
            logger.debug(f"techo parse error: {e}")
        return results

    async def _source_workaway_ngo(self, filters: Dict[str, Any]) -> List[Dict[str, Any]]:
        # Acceso público puede variar; intentamos listar ONG básicas
        url = "https://www.workaway.info/es/hosttype/ngo"
        html = await self._fetch_html(url)
        results: List[Dict[str, Any]] = []
        if not html:
            return results
        try:
            soup = BeautifulSoup(html, "lxml")
            cards = soup.select("a[href*='/en/host/'], a[href*='/es/host/']")
            for a in cards[:50]:  # limitar
                title = a.get_text(strip=True) or "ONG / NGO host"
                href = a.get("href")
                if not href:
                    continue
                results.append({
                    "org": title,
                    "role": "Voluntariado ONG",
                    "location": "global",
                    "need": "diverso",
                    "hours": "variable",
                    "score": 0.6,
                    "source": url,
                    "link": f"https://www.workaway.info{href}" if href.startswith("/") else href,
                    "images": self._extract_images_from_html(html),
                    "posted_at": datetime.now().isoformat()
                })
        except Exception as e:
            logger.debug(f"workaway parse error: {e}")
        return results

    async def _source_worldpackers(self, filters: Dict[str, Any]) -> List[Dict[str, Any]]:
        url = "https://worldpackers.com/es"
        html = await self._fetch_html(url)
        results: List[Dict[str, Any]] = []
        if not html:
            # Fallback directo a Brave si la home no carga (bloqueo/JS)
            try:
                ps = await provider_search.search_boosted(
                    query="voluntariado mexico", topK=10,
                    domains=["site:worldpackers.com/positions"],
                    keywords=["mexico", "méxico", "it", "software", "datos", "programación", "voluntariado"]
                )
                for item in (ps.get("results") or [])[:10]:
                    link = item.get("url")
                    if not link:
                        continue
                    htmlp = await self._fetch_html(link, timeout=15)
                    if not htmlp:
                        continue
                    det = self._extract_worldpackers_position_details(htmlp, link)
                    if det:
                        results.append(det)
                return results
            except Exception:
                return results
        try:
            soup = BeautifulSoup(html, "lxml")
            # Buscar enlaces de búsqueda específicos para México
            search_links = [a.get("href") for a in soup.select("a[href*='/es/search/']") if a.get("href")]
            # Prioriza búsquedas con 'mexico'
            prioritized = [h for h in search_links if "mexico" in h.lower()]
            if not prioritized and search_links:
                prioritized = search_links[:2]
            # Si no hay enlaces de búsqueda, intenta un default conocido
            if not prioritized:
                prioritized = ["/es/search/social_impact/north_america/mexico"]
            # Extrae posiciones de hasta 2 páginas de búsqueda
            for rel in prioritized[:2]:
                search_url = f"https://worldpackers.com{rel}" if rel.startswith("/") else rel
                results.extend(await self._fetch_positions_from_worldpackers(search_url, max_items=12))
            # Si no se obtuvieron resultados, fallback con Brave
            if not results:
                ps = await provider_search.search_boosted(
                    query="voluntariado mexico", topK=10,
                    domains=["site:worldpackers.com/positions"],
                    keywords=["mexico", "méxico", "it", "software", "datos", "programación", "voluntariado"]
                )
                for item in (ps.get("results") or [])[:10]:
                    link = item.get("url")
                    if not link:
                        continue
                    htmlp = await self._fetch_html(link, timeout=15)
                    if not htmlp:
                        continue
                    det = self._extract_worldpackers_position_details(htmlp, link)
                    if det:
                        results.append(det)
        except Exception as e:
            logger.debug(f"worldpackers parse error: {e}")
        return results

    async def _source_ayuda_en_accion(self, filters: Dict[str, Any]) -> List[Dict[str, Any]]:
        url = "https://ayudaenaccion.org.mx/voluntariado/"
        html = await self._fetch_html(url)
        results: List[Dict[str, Any]] = []
        if not html:
            return results
        try:
            soup = BeautifulSoup(html, "lxml")
            sections = [
                ("Voluntariado puntual", "Actividades en grupo y visitas comunitarias"),
                ("Voluntariado profesional", "Talentos profesionales en áreas administrativas y programas"),
                ("Voluntariado profundo", "Actividades alineadas a necesidades vitales por proyecto"),
                ("Voluntariado digital", "Colabora online desde cualquier lugar")
            ]
            for name, desc in sections:
                results.append({
                    "org": "Ayuda en Acción México",
                    "role": name,
                    "location": "CDMX / México",
                    "need": desc,
                    "hours": "según programa",
                    "score": 0.65,
                    "source": url,
                    "link": url,
                    "posted_at": datetime.now().isoformat()
                })
        except Exception as e:
            logger.debug(f"ayudaenaccion parse error: {e}")
        return results

    async def _source_voluntariado_net(self, filters: Dict[str, Any]) -> List[Dict[str, Any]]:
        url = "https://voluntariado.net/tipos-de-voluntariado/"
        html = await self._fetch_html(url)
        results: List[Dict[str, Any]] = []
        if not html:
            return results
        try:
            soup = BeautifulSoup(html, "lxml")
            categories = [
                "ambiental", "comunitario", "cultural", "deportivo", "educativo",
                "internacional", "ocio y tiempo libre", "protección civil", "socio-sanitario", "social"
            ]
            for cat in categories:
                results.append({
                    "org": "Directorio voluntariado.net",
                    "role": f"Voluntariado {cat}",
                    "location": "España / Global",
                    "need": cat,
                    "hours": "variable",
                    "score": 0.5,
                    "source": url,
                    "link": url,
                    "images": self._extract_images_from_html(html),
                    "posted_at": datetime.now().isoformat()
                })
        except Exception as e:
            logger.debug(f"voluntariado.net parse error: {e}")
        return results

    async def _source_local_mock(self, filters: Dict[str, Any]) -> List[Dict[str, Any]]:
        # Simulación local con filtros aplicados
        base = [
            {"org": "Cruz Roja", "role": "Apoyo en salud", "location": "cdmx", "need": "urgente", "hours": "fin de semana"},
            {"org": "Banco de Alimentos", "role": "Logística", "location": "monterrey", "need": "niños", "hours": "medio tiempo"},
            {"org": "ONG Educación", "role": "Mentoría", "location": "guadalajara", "need": "educación", "hours": "remoto"},
        ]
        def match(item):
            ok = True
            if "location" in filters:
                ok &= filters["location"].lower() in item["location"].lower()
            if "field" in filters:
                ok &= filters["field"] in (item["role"].lower() + " " + item.get("need",""))
            if "need" in filters:
                ok &= filters["need"] in (item.get("need",""))
            if "availability" in filters:
                ok &= filters["availability"] in item["hours"].lower()
            return ok
        return [
            {
                **i,
                "score": 0.7,
                "source": "local_mock",
                "posted_at": datetime.now().isoformat()
            } for i in base if match(i)
        ]


volunteer_search = VolunteerSearch()

