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
INCLUDE_KEYWORDS = ("wrócił", "wrocil", "wróciła", "wrocila", "wraca", "pracuje", "jest", "dodaj")

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


def _load_name_to_cid() -> dict:
    """V3.26 hotfix CHANGE 3: zwróć {panel_nick: cid_int} dla confirmation messages.
    Merge identyczny jak _load_names — kurier_ids first, courier_names overrides.
    Gdy ten sam name ma multiple cidy (alias-pair like Grzegorz/Grzegorz R), wybiera
    pierwszy z merged (deterministic: courier_names wins → cid z courier_names.json).
    """
    merged: dict = {}  # cid_str -> name
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
    out: dict = {}
    for cid_str, name in merged.items():
        if name and name not in out:
            try:
                out[name] = int(cid_str)
            except (TypeError, ValueError):
                continue
    return out


def _norm(s: str) -> str:
    return s.lower().replace(".", " ").replace(",", " ")


def _find_courier(text: str, names: List[str]) -> Optional[str]:
    """Match courier name w tekście. Strategia w kolejności:
    1. Pełna nazwa MULTI-WORD substring (najdłuższe pierwsze) — np. "Adrian Cit" w "adrian cit nie pracuje".
       SINGLE-WORD nazwy (np. "Adrian") pomijane tutaj — leciałyby fallthrough do petla 3,
       bo inaczej shadowowałyby legitne "Adrian Cit" / "Adrian R" które nie matchują pełną nazwą
       ale matchują second-token-prefix (V3.26 hotfix BUG 2).
    2. **V3.26 hotfix BUG 2**: drugi-token prefix — np. "Adrian Cit" matchuje "adrian citko ..."
       (text_words[0] == name_words[0] AND text_words[1].startswith(name_words[1]))
    3. Pierwsze słowo fallback (wszystkie names) — np. "Adrian" matchuje samotne "adrian"
       lub "Mykyta K" matchuje "Mykyta nie pracuje" (drugi token "k" nie matchuje second-prefix).
    Zwraca panel name (np. 'Mykyta K' / 'Adrian Cit')."""
    t = " " + " ".join(_norm(text).split()) + " "
    # Petla 1: TYLKO multi-word names (>=2 tokens). Single-word names pomijamy
    # żeby nie shadowować — np. "Adrian" by matchował "adrian citko" zanim
    # petla 2 (second-prefix) ma szansę zwrócić "Adrian Cit".
    for name in sorted(names, key=lambda n: -len(n)):
        n_parts = _norm(name).split()
        if len(n_parts) < 2:
            continue
        n = " ".join(n_parts)
        if n and f" {n} " in t:
            return name
    # V3.26 hotfix BUG 2: second-token prefix match. Zapobiega kolizji
    # "adrian citko" → "Adrian" (cid=21) zamiast "Adrian Cit" (cid=457).
    text_words = _norm(text).split()
    if len(text_words) >= 2:
        for name in sorted(names, key=lambda n: -len(n)):
            name_words = _norm(name).split()
            if (len(name_words) >= 2
                    and text_words[0] == name_words[0]
                    and text_words[1].startswith(name_words[1])
                    and len(name_words[1]) >= 2):  # min 2-char name-2nd-word avoid trivial collision
                return name
    # Petla 3: first-word fallback dla wszystkich names (single + multi).
    for name in names:
        parts = _norm(name).split()
        if not parts:
            continue
        first = parts[0]
        if first and f" {first} " in t:
            return name
    return None


def _resolve_cid(name: str) -> str:
    """V3.26 hotfix CHANGE 3: name → cid string for confirmation. '?' if unknown."""
    try:
        m = _load_name_to_cid()
        cid = m.get(name)
        return str(cid) if cid is not None else "?"
    except Exception:
        return "?"


def parse_command(text: str) -> Tuple[str, str]:
    """Zwraca (action, response). action ∈ {exclude, include, reset, unknown, noop}.

    V3.25 STEP D (R-03 core): dodane slash commands /stop i /wraca jako
    pierwsza warstwa parsing przed legacy keyword detection. Re-używa
    istniejącego flow excluded list (manual_overrides.json) — żaden nowy
    state file nie potrzebny, żaden nowy bot/timer. Live activation wymaga
    restart dispatch-telegram (Adrian ACK).
    """
    raw = (text or "").strip()
    if not raw:
        return "noop", ""
    low = raw.lower()

    # V3.25 STEP D: explicit slash commands /stop + /wraca (R-03 core).
    # Format: "/stop <imię>" / "/wraca <imię>". Imię matchowane fuzzy
    # przez _find_courier (substring + first-word fallback) z names list.
    if low.startswith("/stop"):
        rest = raw[len("/stop"):].strip()
        if not rest:
            return "unknown", "❓ Użycie: /stop <imię kuriera> (np. /stop bartek)"
        names = _load_names()
        courier = _find_courier(rest, names)
        if courier is None:
            return "unknown", f"❓ Nie znalazłem kuriera dla '{rest}'"
        data = load()
        excluded = data.get("excluded", [])
        if courier not in excluded:
            excluded.append(courier)
            data["excluded"] = excluded
            save(data)
        return "exclude", f"🛑 {courier} (cid={_resolve_cid(courier)}) STOP — wykluczony do końca dnia"
    if low.startswith("/wraca") or low.startswith("/wrocil"):
        # /wraca <imię>
        parts = raw.split(maxsplit=1)
        rest = parts[1].strip() if len(parts) > 1 else ""
        if not rest:
            return "unknown", "❓ Użycie: /wraca <imię kuriera> (np. /wraca bartek)"
        names = _load_names()
        courier = _find_courier(rest, names)
        if courier is None:
            return "unknown", f"❓ Nie znalazłem kuriera dla '{rest}'"
        data = load()
        excluded = data.get("excluded", [])
        if courier in excluded:
            excluded.remove(courier)
            data["excluded"] = excluded
            save(data)
        return "include", f"✅ {courier} (cid={_resolve_cid(courier)}) wrócił do flow"

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
        return "exclude", f"✅ {courier} (cid={_resolve_cid(courier)}) wykluczony do końca dnia"
    # has_include
    if courier in excluded:
        excluded.remove(courier)
        data["excluded"] = excluded
        save(data)
    return "include", f"✅ {courier} (cid={_resolve_cid(courier)}) przywrócony"
