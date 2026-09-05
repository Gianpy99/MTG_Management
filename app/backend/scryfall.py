"""Server-side Scryfall proxy with a persistent, throttled cache.

The browser calls our own API (same origin → no CORS). The backend fetches from
Scryfall once per card, prefers a Middle-earth printing, and caches the slim
result to a JSON file in the data volume so redeploys keep the cache.
"""
from __future__ import annotations

import json
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

from database import DATA_DIR

CACHE_FILE = DATA_DIR / "scryfall_cache.json"
HEADERS = {"User-Agent": "MTGManagement/1.0 (personal Raspberry Pi app)", "Accept": "application/json"}
ME_FILTER = "(set:ltr or set:ltc or set:hob or set:hoc)"

_lock = threading.Lock()
_last_call = 0.0
_cache: dict[str, dict] = {}

if CACHE_FILE.exists():
    try:
        _cache = json.loads(CACHE_FILE.read_text(encoding="utf-8"))
    except Exception:
        _cache = {}


def _save() -> None:
    try:
        CACHE_FILE.write_text(json.dumps(_cache), encoding="utf-8")
    except Exception:
        pass


def _throttle() -> None:
    global _last_call
    wait = 0.12 - (time.time() - _last_call)
    if wait > 0:
        time.sleep(wait)
    _last_call = time.time()


def _fetch(url: str) -> dict | None:
    for attempt in range(4):
        _throttle()
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers=HEADERS), timeout=10) as r:
                return json.load(r)
        except urllib.error.HTTPError as e:
            if e.code == 404:
                return None
            if e.code == 429:
                time.sleep(1.0 + attempt)
                continue
            return None
        except Exception:
            return None
    return None


def _slim(c: dict) -> dict:
    faces = c.get("card_faces") or []
    imgs = c.get("image_uris") or (faces[0].get("image_uris") if faces else {}) or {}
    return {
        "name": c.get("name", ""),
        "flavor_name": c.get("flavor_name", ""),
        "type_line": c.get("type_line") or (faces[0].get("type_line", "") if faces else ""),
        "mana_cost": c.get("mana_cost") or (faces[0].get("mana_cost", "") if faces else ""),
        "oracle_text": c.get("oracle_text")
        or ("\n//\n".join(f.get("oracle_text", "") for f in faces) if faces else ""),
        "image": imgs.get("normal") or imgs.get("large") or "",
        "image_small": imgs.get("small") or imgs.get("normal") or "",
        "scryfall_uri": c.get("scryfall_uri", ""),
        "cardmarket": (c.get("purchase_uris") or {}).get("cardmarket", ""),
    }


def get_card(name: str) -> dict:
    key = name.strip().lower()
    cached = _cache.get(key)
    if cached is not None:
        return cached
    with _lock:
        cached = _cache.get(key)
        if cached is not None:
            return cached
        # Prefer a Middle-earth printing, fall back to fuzzy match.
        d = _fetch(
            "https://api.scryfall.com/cards/search?"
            + urllib.parse.urlencode(
                {"q": f'!"{name}" {ME_FILTER}', "unique": "prints", "order": "released", "dir": "desc"}
            )
        )
        card = d["data"][0] if d and d.get("data") else None
        if card is None:
            card = _fetch(
                "https://api.scryfall.com/cards/named?" + urllib.parse.urlencode({"fuzzy": name})
            )
        slim = _slim(card) if card else {"name": name, "not_found": True}
        _cache[key] = slim
        _save()
        return slim
