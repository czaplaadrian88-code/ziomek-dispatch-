"""Manual courier overrides — wykluczanie kurierów z dispatch via Telegram free-text.

Persist: /root/.openclaw/workspace/dispatch_state/manual_overrides.json
Format: {"excluded": ["Mykyta K", ...], "updated_at": "<iso>"}

Lifecycle: do końca dnia (reset codziennie rano przez cron lub ręcznie "reset").
"""
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional, Tuple

OVERRIDES_PATH = "/root/.openclaw/workspace/dispatch_state/manual_overrides.json"
COURIER_NAMES_PATH = "/root/.openclaw/workspace/dispatch_state/courier_names.json"
KURIER_IDS_PATH = "/root/.openclaw/workspace/dispatch_state/kurier_ids.json"  # V3.25 inverse fallback

EXCLUDE_KEYWORDS = ("nie pracuje", "wyklucz", "choruje", "nie ma")
INCLUDE_KEYWORDS = ("wrócił", "wrocil", "wróciła", "wrocila", "pracuje", "jest", "dodaj")

UNKNOWN_MSG = "❓ Nie rozumiem. Przykład: 'Mykyta nie pracuje' lub 'Mykyta wrócił'"


def load() -> dict:
    try:
        with open(OVERRIDES_PATH) as f:
            d = json.load(f)
    except Exception:
        d = {}
    if not isinstance(d, dict):
        d = {}
    d.setdefault("excluded", [])
    d.setdefault("updated_at", "")
    return d


def save(data: dict) -> None:
    data["updated_at"] = datetime.now(timezone.utc).isoformat()
    Path(OVERRIDES_PATH).parent.mkdir(parents=True, exist_ok=True)
    tmp = OVERRIDES_PATH + ".tmp"
    with open(tmp, "w") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, OVERRIDES_PATH)


def get_excluded() -> List[str]:
    return list(load().get("excluded", []))


def _load_names() -> List[str]:
    """V3.25 (STEP A.2): MERGE inverse(kurier_ids) + courier_names. courier_names wins.
    Returns deduplicated list of name strings."""
    merged: dict = {}
    try:
        with open(KURIER_IDS_PATH) as f:
            ids = json.load(f)
        for name, cid in ids.items():
            cid_str = str(cid)
            if cid_str not in merged:
                merged[cid_str] = name
    except Exception:
        pass
    try:
        with open(COURIER_NAMES_PATH) as f:
            d = json.load(f)
        for cid_str, name in d.items():
            merged[cid_str] = name
    except Exception:
        pass
    # Dedupe values (różne cid mogą mieć tę samą nazwę po V3.25 alias-pair)
    return sorted({v for v in merged.values() if v})


def _norm(s: str) -> str:
    return s.lower().replace(".", " ").replace(",", " ")


def _find_courier(text: str, names: List[str]) -> Optional[str]:
    """Match courier name w tekście. Najpierw pełna nazwa (najdłuższe pierwsze),
    potem pierwsze słowo. Zwraca panel name (np. 'Mykyta K')."""
    t = " " + " ".join(_norm(text).split()) + " "
    for name in sorted(names, key=lambda n: -len(n)):
        n = " ".join(_norm(name).split())
        if n and f" {n} " in t:
            return name
    for name in names:
        parts = _norm(name).split()
        if not parts:
            continue
        first = parts[0]
        if first and f" {first} " in t:
            return name
    return None


def parse_command(text: str) -> Tuple[str, str]:
    """Zwraca (action, response). action ∈ {exclude, include, reset, unknown, noop}."""
    raw = (text or "").strip()
    if not raw:
        return "noop", ""
    low = raw.lower()
    if low in ("reset", "reset overrides"):
        data = load()
        data["excluded"] = []
        save(data)
        return "reset", "✅ Reset — wszyscy kurierzy aktywni"

    has_exclude = any(kw in low for kw in EXCLUDE_KEYWORDS)
    has_include = (not has_exclude) and any(kw in low for kw in INCLUDE_KEYWORDS)

    if not (has_exclude or has_include):
        return "unknown", UNKNOWN_MSG

    names = _load_names()
    courier = _find_courier(raw, names)
    if courier is None:
        return "unknown", UNKNOWN_MSG

    data = load()
    excluded = data.get("excluded", [])
    if has_exclude:
        if courier not in excluded:
            excluded.append(courier)
            data["excluded"] = excluded
            save(data)
        return "exclude", f"✅ {courier} wykluczony do końca dnia"
    # has_include
    if courier in excluded:
        excluded.remove(courier)
        data["excluded"] = excluded
        save(data)
    return "include", f"✅ {courier} przywrócony"
