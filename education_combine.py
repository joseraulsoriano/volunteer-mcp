#!/usr/bin/env python3
import json
import os
import unicodedata
from typing import Any, Dict, List, Tuple


def _normalize_text(text: str) -> str:
    if not isinstance(text, str):
        text = str(text or "")
    # lower, strip, remove accents
    text = text.strip().lower()
    text = unicodedata.normalize("NFD", text)
    text = "".join(ch for ch in text if unicodedata.category(ch) != "Mn")
    # collapse spaces
    text = " ".join(text.split())
    return text


def _make_key(name: str, state: str = "") -> str:
    return f"{_normalize_text(name)}|{_normalize_text(state)}"


def _load_json(path: str) -> Any:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def combine(enriched_path: str, details_path: str) -> List[Dict[str, Any]]:
    enriched: List[Dict[str, Any]] = _load_json(enriched_path) or []
    details: List[Dict[str, Any]] = _load_json(details_path) or []

    # Index details by (nombre,state?) with fallbacks
    details_index: Dict[str, Dict[str, Any]] = {}
    for d in details:
        nombre = d.get("nombre") or d.get("name") or ""
        tipo = d.get("type")
        # Best effort: try to find state from ubicacion missing; keep empty
        key = _make_key(nombre, "")
        details_index[key] = d

    combined: List[Dict[str, Any]] = []
    for e in enriched:
        state = e.get("state") or ""
        name = e.get("name") or e.get("nombre") or ""
        key_exact: str = _make_key(name, state)
        key_no_state: str = _make_key(name, "")
        d = details_index.get(key_exact) or details_index.get(key_no_state)

        merged = dict(e)
        if d:
            # Flatten commonly needed fields
            if "carreras" in d:
                merged["carreras"] = d.get("carreras")
            if "costo" in d:
                merged["costo"] = d.get("costo")
            if "ubicacion" in d and d.get("ubicacion"):
                merged["ubicacion"] = d.get("ubicacion")
            # Keep full details under a subkey too
            merged["details_raw"] = d
        combined.append(merged)

    return combined


def main() -> None:
    import argparse
    p = argparse.ArgumentParser(description="Combina edu_enriched.json y edu_details.json en un solo JSON")
    p.add_argument("--enriched", dest="enriched_path", default="data/edu_enriched.json")
    p.add_argument("--details", dest="details_path", default="data/edu_details.json")
    p.add_argument("--out", dest="out_path", default="data/edu_all.json")
    args = p.parse_args()

    os.makedirs(os.path.dirname(args.out_path) or ".", exist_ok=True)
    combined = combine(args.enriched_path, args.details_path)
    with open(args.out_path, "w", encoding="utf-8") as f:
        json.dump(combined, f, ensure_ascii=False, indent=2)
    print(args.out_path)


if __name__ == "__main__":
    main()


