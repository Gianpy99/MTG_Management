"""Cardmarket price lookup, resolved in bulk via Scryfall's collection API.

Fetching prices one card at a time does not scale: a filtered collection view
can hold hundreds of rows, and one HTTP round-trip per row saturates the server
thread pool and stalls the whole UI. Scryfall's ``/cards/collection`` endpoint
accepts 75 identifiers per call, so a full set resolves in a handful of
requests instead of hundreds.

Cards are identified by *name + set code*. That resolves to the set's main
("V.1") printing, which is the price worth comparing against — a plain name
search can land on a showcase/alternate-art variant that costs many times more.
Anything that fails to resolve is retried by name alone.

Results are cached to a JSON file in the data volume (same place as the SQLite
DB) so prices survive restarts and redeploys, with a TTL so they stay roughly
current.
"""
from __future__ import annotations

import json
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

from database import DATA_DIR

CACHE_FILE = DATA_DIR / "price_cache.json"
HEADERS = {
    "User-Agent": "MTGManagement/1.0 (personal Raspberry Pi app)",
    "Accept": "application/json",
    "Content-Type": "application/json",
}
COLLECTION_URL = "https://api.scryfall.com/cards/collection"
CHUNK = 75  # Scryfall's documented maximum identifiers per collection request.
TTL_SECONDS = 7 * 24 * 3600  # Cardmarket prices drift; refresh weekly.

# The workbook stores display names; Scryfall needs set codes. ``edition`` is
# the code when the Scryfall enrichment has run, otherwise fall back to this.
SET_CODES = {
    "the lord of the rings": "ltr",
    "the lord of the rings: tales of middle-earth": "ltr",
    "tales of middle-earth": "ltr",
    "the lord of the rings commander": "ltc",
    "the hobbit": "hob",
    "the hobbit eternal": "hoc",
}

_lock = threading.Lock()
_last_call = 0.0
# key -> {"eur": float | None, "url": str, "ts": float}
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
    wait = 0.1 - (time.time() - _last_call)
    if wait > 0:
        time.sleep(wait)
    _last_call = time.time()


def set_code(edition: str, set_name: str) -> str:
    code = (edition or "").strip().lower()
    if code:
        return code
    return SET_CODES.get((set_name or "").strip().lower(), "")


def cache_key(edition: str, set_name: str, name: str) -> str:
    """Identify the set's main printing, falling back to the bare card name."""
    nm = (name or "").strip().lower()
    code = set_code(edition, set_name)
    return f"{code}:{nm}" if code else f"name:{nm}"


def _price_of(card: dict) -> dict:
    prices = card.get("prices") or {}
    eur = prices.get("eur") or prices.get("eur_foil")
    return {
        "eur": float(eur) if eur else None,
        "url": (card.get("purchase_uris") or {}).get("cardmarket", ""),
        "ts": time.time(),
    }


def _post_collection(identifiers: list[dict]) -> list[dict]:
    body = json.dumps({"identifiers": identifiers}).encode("utf-8")
    for attempt in range(3):
        _throttle()
        try:
            req = urllib.request.Request(
                COLLECTION_URL, data=body, headers=HEADERS, method="POST"
            )
            with urllib.request.urlopen(req, timeout=20) as r:
                return (json.load(r) or {}).get("data") or []
        except urllib.error.HTTPError as e:
            if e.code == 429:
                time.sleep(1.0 + attempt)
                continue
            return []
        except Exception:
            return []
    return []


def _get(url: str) -> dict | None:
    for attempt in range(3):
        _throttle()
        try:
            req = urllib.request.Request(url, headers=HEADERS)
            with urllib.request.urlopen(req, timeout=15) as r:
                return json.load(r)
        except urllib.error.HTTPError as e:
            if e.code == 429:
                time.sleep(1.0 + attempt)
                continue
            return None
        except Exception:
            return None
    return None


def _search_price(name: str) -> dict | None:
    """Cheapest priced paper printing, for cards the collection lookup can't price.

    Cards with no set code (imported as 'Unknown') otherwise resolve to an
    arbitrary printing that carries no EUR price at all.
    """
    url = "https://api.scryfall.com/cards/search?" + urllib.parse.urlencode(
        {"q": f'!"{name}" -is:digital', "unique": "prints", "order": "eur", "dir": "asc"}
    )
    data = _get(url)
    for card in (data or {}).get("data", []):
        priced = _price_of(card)
        if priced["eur"] is not None:
            return priced
    return None


def _fresh(entry: dict | None) -> bool:
    return bool(entry) and (time.time() - entry.get("ts", 0)) < TTL_SECONDS


def _front(name: str) -> str:
    """Scryfall indexes double-faced cards by their front-face name."""
    return (name or "").split(" // ")[0].strip()


def _resolve(keys: list[str], names: dict[str, str], use_set: bool) -> set[str]:
    """Fetch ``keys`` in chunks of 75; return the subset that got a real price."""
    found: set[str] = set()
    for i in range(0, len(keys), CHUNK):
        chunk = keys[i : i + CHUNK]
        identifiers = []
        for k in chunk:
            code = k.split(":", 1)[0]
            ident = {"name": _front(names[k])}
            if use_set and code != "name":
                ident["set"] = code
            identifiers.append(ident)
        by_name: dict[str, dict] = {}
        for card in _post_collection(identifiers):
            priced = _price_of(card)
            full = (card.get("name") or "").strip().lower()
            by_name[full] = priced
            by_name.setdefault(_front(full), priced)
        for k in chunk:
            priced = by_name.get(_front(names[k]).lower())
            # Treat a priceless hit as unresolved so the next stage can retry.
            if priced and priced["eur"] is not None:
                _cache[k] = priced
                found.add(k)
    return found


def get_prices(cards: list[tuple[str, str, str]]) -> dict[str, dict]:
    """Resolve prices for ``(edition, set_name, card_name)`` triples.

    Returns a ``cache_key -> {"eur", "url"}`` map. Cached entries are returned
    immediately; only stale/missing ones hit the network, in chunks of 75.
    """
    names: dict[str, str] = {}  # key -> card name used as the identifier
    for edition, set_name, name in cards:
        names.setdefault(cache_key(edition, set_name, name), name)

    missing = [k for k in names if not _fresh(_cache.get(k))]
    if missing:
        with _lock:
            # Re-check under the lock: a concurrent request may have filled these.
            missing = [k for k in missing if not _fresh(_cache.get(k))]
            if missing:
                # 1. name + set code → the set's main ("V.1") printing.
                found = _resolve(missing, names, use_set=True)
                # 2. name alone, for rows with a missing/incorrect set code.
                retry = [k for k in missing if k not in found]
                if retry:
                    found |= _resolve(retry, names, use_set=False)
                # 3. cheapest priced paper printing, for anything still unpriced.
                for k in (k for k in missing if k not in found):
                    priced = _search_price(_front(names[k]))
                    _cache[k] = priced or {"eur": None, "url": "", "ts": time.time()}
                _save()  # one write per batch, not per card

    return {
        k: {"eur": v.get("eur"), "url": v.get("url", "")}
        for k in names
        if (v := _cache.get(k))
    }
