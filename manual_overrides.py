"""Manual courier overrides — wykluczanie kurierów z dispatch via Telegram free-text.

Persist: /root/.openclaw/workspace/dispatch_state/manual_overrides.json
Format: {"excluded": ["Mykyta K", ...], "updated_at": "<iso>"}

Lifecycle: do końca dnia (reset codziennie rano przez cron lub ręcznie "reset").
"""
import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional, Tuple
from zoneinfo import ZoneInfo

_WAW = ZoneInfo("Europe/Warsaw")

OVERRIDES_PATH = "/root/.openclaw/workspace/dispatch_state/manual_overrides.json"
COURIER_NAMES_PATH = "/root/.openclaw/workspace/dispatch_state/courier_names.json"
KURIER_IDS_PATH = "/root/.openclaw/workspace/dispatch_state/kurier_ids.json"  # V3.25 inverse fallback
# PANEL-CANON desync fix (2026-06-10): grafik = {pełne imię: cid}. courier_resolver
# od 06-10 nadaje flocie pełne imię z grafiku jako cs.name (commit bb9bc27), więc
# egzekucja wykluczenia po nazwie (cs.name in excluded) gubiła skrót panelowy
# zapisany tutaj (np. "Mateusz O" ≠ "Mateusz Ostapczuk"). Czytamy ten plik, by
# zmapować dowolną formę nazwy → cid (get_excluded_cids → match po cid).
GRAFIK_FULL_NAMES_PATH = "/root/.openclaw/workspace/dispatch_state/grafik_full_names.json"

EXCLUDE_KEYWORDS = ("nie pracuje", "wyklucz", "choruje", "nie ma")
INCLUDE_KEYWORDS = ("wrócił", "wrocil", "wróciła", "wrocila", "wraca", "pracuje", "jest", "dodaj")
# 2026-06-01: podzbiór INCLUDE_KEYWORDS który DODAJE do grafiku (working-override),
# nie tylko zdejmuje ze STOP. "jest" celowo pominięte — zbyt słabe ("gdzie jest X")
# żeby tworzyć syntetyczny wpis grafiku; "jest" zostaje czystym un-exclude (legacy).
_WORKING_ADD_KEYWORDS = ("wrócił", "wrocil", "wróciła", "wrocila", "wraca", "pracuje", "dodaj")

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
    # zalążek B (2026-06-10): cid jawnie zapisany przy /stop — egzekucja po cid
    # (dispatchable_fleet) odporna na desync nick↔pełne imię z grafiku.
    d.setdefault("excluded_cids", [])
    if not isinstance(d["excluded_cids"], list):
        d["excluded_cids"] = []
    d.setdefault("working", {})
    if not isinstance(d["working"], dict):
        d["working"] = {}
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


def get_working() -> Dict[str, dict]:
    """Working-override (2026-06-01): {cid_str: {"start": "HH:MM", "end": "HH:MM", ...}}.

    Syntetyczne wpisy grafiku z komendy "X pracuje" — cid-keyed (jednoznaczne, bez
    fuzzy name-match). Konsumowane w courier_resolver.dispatchable_fleet jako
    autorytatywna gałąź (kurier spoza grafiku staje się dispatchowalny). Lifecycle:
    do końca dnia (reset 06:00 via manual_overrides_daily_reset). Zwraca kopię."""
    w = load().get("working", {})
    return dict(w) if isinstance(w, dict) else {}


def _all_name_to_cid() -> Dict[str, int]:
    """Wyczerpujący {name: cid_int} z WSZYSTKICH źródeł nazw — łapie zarówno skrót
    panelowy (kurier_ids forward + courier_names inverse) JAK I pełne imię z grafiku
    (grafik_full_names forward). Odporne na desync 2026-06-10 (flota nazywa cid
    pełnym imieniem, override trzyma skrót). Fail-soft per źródło."""
    out: Dict[str, int] = {}
    # kurier_ids.json: {name: cid} (zawiera i skrót i pełne imię od 06-10)
    try:
        with open(KURIER_IDS_PATH) as f:
            for name, cid in json.load(f).items():
                if isinstance(name, str) and name.strip():
                    try:
                        out[name] = int(cid)
                    except (TypeError, ValueError):
                        pass
    except Exception:
        pass
    # courier_names.json: {cid: name} → inverse
    try:
        with open(COURIER_NAMES_PATH) as f:
            for cid_str, name in json.load(f).items():
                if isinstance(name, str) and name.strip():
                    try:
                        out[name] = int(cid_str)
                    except (TypeError, ValueError):
                        pass
    except Exception:
        pass
    # grafik_full_names.json: {pełne imię: cid}
    try:
        with open(GRAFIK_FULL_NAMES_PATH, encoding="utf-8") as f:
            for name, cid in json.load(f).items():
                if isinstance(name, str) and name.strip():
                    try:
                        out[name] = int(cid)
                    except (TypeError, ValueError):
                        pass
    except Exception:
        pass
    return out


def get_excluded_cids() -> set:
    """Zbiór cid (str) wykluczonych kurierów — autorytatywne źródło egzekucji w
    dispatchable_fleet (match po cid, NIE po nazwie). Łączy:
    - cid jawnie zapisane przy /stop (excluded_cids, zalążek B),
    - cid zmapowane z nazw na liście `excluded` (Opcja A — wsteczna zgodność +
      naprawa LIVE: stary wpis 'Mateusz O' rozwiązuje się na cid 413 bez ponownego
      /stop, mimo że flota nazywa go 'Mateusz Ostapczuk').
    Fail-soft: gdy mapowanie nazwy → cid nieznane, nazwa zostaje backstopem w
    name-match (courier_resolver sprawdza OBA)."""
    d = load()
    out: set = set()
    for c in d.get("excluded_cids", []) or []:
        cs = str(c).strip()
        if cs:
            out.add(cs)
    try:
        name2cid = _all_name_to_cid()
    except Exception:
        name2cid = {}
    for name in d.get("excluded", []) or []:
        cid = name2cid.get(name)
        if cid is not None:
            out.add(str(cid))
    return out


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


def _now_warsaw_hhmm() -> str:
    return datetime.now(_WAW).strftime("%H:%M")


def _default_end() -> str:
    """Domyślny koniec syntetycznej zmiany. Env override WORKING_OVERRIDE_DEFAULT_END
    (czytane przy każdym wywołaniu → spójne z common.py + test-friendly)."""
    return os.environ.get("WORKING_OVERRIDE_DEFAULT_END", "24:00")


def _parse_shift_bounds(text: str) -> Tuple[str, str, bool]:
    """Free-text → (start_hhmm, end_hhmm, end_explicit). Default start=teraz (Warsaw),
    end=DEFAULT_END, end_explicit=False.

    Rozpoznaje opcjonalne 'od HH[:MM]' (start) oraz 'do HH[:MM]' (end), np.
    "Adrian pracuje do 22" → end 22:00; "Bartek pracuje od 15:30 do 23" → 15:30–23:00.
    Tolerancyjne — przy błędnym zakresie zostawia default.

    end_explicit=True gdy operator JAWNIE podał 'do HH[:MM]' (świadoma decyzja o końcu).
    courier_resolver GRAFIK-CAP (2026-06-07) używa tego flagu, by NIE przycinać jawnego
    końca do końca realnego grafiku — domyślny 24:00 jest przycinany, jawny respektowany."""
    low = (text or "").lower()
    start = _now_warsaw_hhmm()
    end = _default_end()
    end_explicit = False
    m_od = re.search(r"\bod\s+(\d{1,2})(?::(\d{2}))?", low)
    if m_od:
        h = int(m_od.group(1))
        mm = int(m_od.group(2) or 0)
        if 0 <= h <= 23 and 0 <= mm <= 59:
            start = f"{h:02d}:{mm:02d}"
    m_do = re.search(r"\bdo\s+(\d{1,2})(?::(\d{2}))?", low)
    if m_do:
        h = int(m_do.group(1))
        mm = int(m_do.group(2) or 0)
        if h == 24 and mm == 0:
            end = "24:00"
            end_explicit = True
        elif 0 <= h <= 23 and 0 <= mm <= 59:
            end = f"{h:02d}:{mm:02d}"
            end_explicit = True
    return start, end, end_explicit


def _add_working(data: dict, courier: str, text: str) -> Optional[Tuple[str, str, str]]:
    """Dodaj cid-keyed working entry dla 'X pracuje'. Returns (cid_str, start, end) lub
    None gdy cid nieznany (bez cid nie da się zakotwiczyć override'a → caller informuje
    operatora żeby użył /dopisz). Mutuje data (caller zapisuje przez save)."""
    cid = _resolve_cid(courier)
    if cid == "?":
        return None
    start, end, end_explicit = _parse_shift_bounds(text)
    working = data.setdefault("working", {})
    if not isinstance(working, dict):
        working = {}
        data["working"] = working
    working[cid] = {
        "start": start,
        "end": end,
        "end_explicit": end_explicit,
        "name": courier,
        "added_at": datetime.now(timezone.utc).isoformat(),
    }
    return cid, start, end


def _do_include(data: dict, courier: str, text: str, add_to_grafik: bool = True) -> Tuple[str, str]:
    """Wspólna ścieżka 'pracuje/wrócił/wraca/dodaj' + /wraca + /pracuje. Realizuje OBA
    przypadki Adriana: (1) zdejmij z excluded (powracający po /stop), (2) gdy add_to_grafik
    → dodaj working override (spoza grafiku / zaczyna teraz). Working jest FALLBACKIEM —
    courier_resolver użyje go tylko gdy kurier NIE jest na realnej zmianie (nie rozszerza
    godzin powracającego, który jest w grafiku). Uczciwe potwierdzenie."""
    excluded = data.get("excluded", [])
    was_excluded = courier in excluded
    if was_excluded:
        excluded.remove(courier)
    # zalążek B (2026-06-10): rozwiąż cid odpornie (skrót LUB pełne imię) i zdejmij
    # ze STOP po cid — usuń WSZYSTKIE nazwy mapujące na ten cid (desync nick/full)
    # oraz cid z excluded_cids. Dzięki temu "X wrócił" pełnym imieniem zdejmuje też
    # wpis zapisany skrótem (i odwrotnie).
    try:
        _inc_cid = _all_name_to_cid().get(courier)
    except Exception:
        _inc_cid = None
    if _inc_cid is not None:
        try:
            _n2c = _all_name_to_cid()
        except Exception:
            _n2c = {}
        before = len(excluded)
        excluded = [n for n in excluded if _n2c.get(n) != _inc_cid]
        if len(excluded) != before:
            was_excluded = True
        ec = data.get("excluded_cids", [])
        if isinstance(ec, list) and str(_inc_cid) in [str(c) for c in ec]:
            data["excluded_cids"] = [c for c in ec if str(c) != str(_inc_cid)]
            was_excluded = True
    data["excluded"] = excluded
    added = _add_working(data, courier, text) if add_to_grafik else None
    save(data)
    if added is not None:
        cid, start, end = added
        end_disp = "końca dnia" if end == "24:00" else end
        prefix = "✅" if not was_excluded else "✅ (zdjęty ze STOP)"
        return "include", (f"{prefix} {courier} (cid={cid}) pracuje dziś "
                           f"({start}–{end_disp}) — będę go proponował")
    if add_to_grafik:
        # próbowaliśmy dodać do grafiku, ale cid nieznany
        if was_excluded:
            return "include", (f"✅ {courier} przywrócony (zdjęty ze STOP). "
                               f"⚠️ Brak cid — jeśli nie ma go w grafiku, dodaj: /dopisz <cid> <imię>")
        return "include", (f"⚠️ {courier}: nie znam cid — nie dodam do grafiku. "
                           f"Użyj /dopisz <cid> <imię>")
    # add_to_grafik False (np. samo 'jest') — tylko zdjęcie ze STOP (legacy)
    if was_excluded:
        return "include", f"✅ {courier} (cid={_resolve_cid(courier)}) przywrócony"
    return "include", f"✅ {courier} (cid={_resolve_cid(courier)}) — aktywny"


def _do_exclude(data: dict, courier: str) -> Tuple[str, str]:
    """Wspólna ścieżka 'nie pracuje/wyklucz/choruje' + /stop. Dodaj do excluded ORAZ
    usuń ewentualny working override (operator zatrzymał kuriera — czyść stan)."""
    excluded = data.get("excluded", [])
    if courier not in excluded:
        excluded.append(courier)
        data["excluded"] = excluded
    cid = _resolve_cid(courier)
    # zalążek B (2026-06-10): zapisz cid jawnie → egzekucja po cid w dispatchable_fleet
    # odporna na desync nazw (panel-nick 'Mateusz O' vs grafik 'Mateusz Ostapczuk').
    if cid != "?":
        ec = data.get("excluded_cids", [])
        if not isinstance(ec, list):
            ec = []
        if cid not in ec:
            ec.append(cid)
        data["excluded_cids"] = ec
    working = data.get("working", {})
    if isinstance(working, dict):
        working.pop(cid, None)
        data["working"] = working
    save(data)
    return "exclude", f"🛑 {courier} (cid={cid}) STOP — wykluczony do końca dnia"


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
        return _do_exclude(load(), courier)
    if low.startswith("/wraca") or low.startswith("/wrocil") or low.startswith("/pracuje"):
        # /wraca <imię> | /pracuje <imię> [od HH:MM] [do HH:MM]
        parts = raw.split(maxsplit=1)
        rest = parts[1].strip() if len(parts) > 1 else ""
        if not rest:
            return "unknown", "❓ Użycie: /pracuje <imię> [do HH:MM] (np. /pracuje bartek do 22)"
        names = _load_names()
        courier = _find_courier(rest, names)
        if courier is None:
            return "unknown", f"❓ Nie znalazłem kuriera dla '{rest}'"
        return _do_include(load(), courier, raw)

    if low in ("reset", "reset overrides"):
        data = load()
        data["excluded"] = []
        data["excluded_cids"] = []
        data["working"] = {}
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
    if has_exclude:
        return _do_exclude(data, courier)
    # has_include — 'pracuje/wrócił/wraca/dodaj' dodają do grafiku; samo 'jest' tylko un-exclude
    _add_grafik = any(kw in low for kw in _WORKING_ADD_KEYWORDS)
    return _do_include(data, courier, raw, add_to_grafik=_add_grafik)
