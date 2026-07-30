"""Manual courier overrides — wykluczanie kurierów z dispatch via Telegram free-text.

Persist: /root/.openclaw/workspace/dispatch_state/manual_overrides.json
Format: {"excluded": ["Mykyta K", ...], "updated_at": "<iso>"}

Lifecycle: do końca dnia (reset codziennie rano przez cron lub ręcznie "reset").
"""
import json
import os
import re
from datetime import datetime, timezone
from typing import Dict, List, Optional, Tuple
from zoneinfo import ZoneInfo

from dispatch_v2.common import decision_flag

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
_LEGACY_REVISION_KEY = "legacy_updated_at"

EXCLUDE_KEYWORDS = ("nie pracuje", "wyklucz", "choruje", "nie ma")
INCLUDE_KEYWORDS = ("wrócił", "wrocil", "wróciła", "wrocila", "wraca", "pracuje", "jest", "dodaj")
# 2026-06-01: podzbiór INCLUDE_KEYWORDS który DODAJE do grafiku (working-override),
# nie tylko zdejmuje ze STOP. "jest" celowo pominięte — zbyt słabe ("gdzie jest X")
# żeby tworzyć syntetyczny wpis grafiku; "jest" zostaje czystym un-exclude (legacy).
_WORKING_ADD_KEYWORDS = ("wrócił", "wrocil", "wróciła", "wrocila", "wraca", "pracuje", "dodaj")

UNKNOWN_MSG = "❓ Nie rozumiem. Przykład: 'Mykyta nie pracuje' lub 'Mykyta wrócił'"


def _availability_contract_enabled() -> bool:
    """Fail-closed gate: brak/awaria flags.json = dokładne zachowanie legacy."""
    try:
        return decision_flag("ENABLE_CID_AVAILABILITY_CONTRACT")
    except Exception:
        return False


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
    d.setdefault(_LEGACY_REVISION_KEY, "")
    return d


def save(data: dict) -> None:
    """Compatibility writer delegating unconditionally to the store owner.

    Safety (lock/CAS/preserve CID authority) is independent of the behavior
    kill-switch. A stale caller is rejected rather than losing another writer.
    """

    from dispatch_v2 import courier_availability as _availability

    _availability.save_legacy_payload(data, path=OVERRIDES_PATH)


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


def _now_warsaw_hhmm(at: Optional[datetime] = None) -> str:
    moment = at or datetime.now(_WAW)
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return moment.astimezone(_WAW).strftime("%H:%M")


def _default_end() -> str:
    """Domyślny koniec syntetycznej zmiany. Env override WORKING_OVERRIDE_DEFAULT_END
    (czytane przy każdym wywołaniu → spójne z common.py + test-friendly)."""
    return os.environ.get("WORKING_OVERRIDE_DEFAULT_END", "24:00")


def _parse_shift_bounds(
    text: str,
    *,
    at: Optional[datetime] = None,
) -> Tuple[str, str, bool]:
    """Free-text → (start_hhmm, end_hhmm, end_explicit). Default start=teraz (Warsaw),
    end=DEFAULT_END, end_explicit=False.

    Rozpoznaje opcjonalne 'od HH[:MM]' (start) oraz 'do HH[:MM]' (end), np.
    "Adrian pracuje do 22" → end 22:00; "Bartek pracuje od 15:30 do 23" → 15:30–23:00.
    Tolerancyjne — przy błędnym zakresie zostawia default.

    end_explicit=True gdy operator JAWNIE podał 'do HH[:MM]' (świadoma decyzja o końcu).
    courier_resolver GRAFIK-CAP (2026-06-07) używa tego flagu, by NIE przycinać jawnego
    końca do końca realnego grafiku — domyślny 24:00 jest przycinany, jawny respektowany."""
    low = (text or "").lower()
    start = _now_warsaw_hhmm(at)
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


def _add_working(
    data: dict,
    courier: str,
    text: str,
    *,
    at: Optional[datetime] = None,
    resolved_cid: Optional[str] = None,
) -> Optional[Tuple[str, str, str]]:
    """Dodaj cid-keyed working entry dla 'X pracuje'. Returns (cid_str, start, end) lub
    None gdy cid nieznany (bez cid nie da się zakotwiczyć override'a → caller informuje
    operatora żeby użył /dopisz). Mutuje data (caller zapisuje przez save)."""
    cid = resolved_cid if resolved_cid is not None else _resolve_cid(courier)
    if cid == "?":
        return None
    when = at or datetime.now(timezone.utc)
    if when.tzinfo is None:
        when = when.replace(tzinfo=timezone.utc)
    when = when.astimezone(timezone.utc)
    start, end, end_explicit = _parse_shift_bounds(text, at=when)
    working = data.setdefault("working", {})
    if not isinstance(working, dict):
        working = {}
        data["working"] = working
    working[cid] = {
        "start": start,
        "end": end,
        "end_explicit": end_explicit,
        "name": courier,
        "added_at": when.isoformat(),
    }
    return cid, start, end


def _do_include(data: dict, courier: str, text: str, add_to_grafik: bool = True) -> Tuple[str, str]:
    """Wspólna ścieżka 'pracuje/wrócił/wraca/dodaj' + /wraca + /pracuje. Realizuje OBA
    przypadki Adriana: (1) zdejmij z excluded (powracający po /stop), (2) gdy add_to_grafik
    → dodaj working override (spoza grafiku / zaczyna teraz). Working jest FALLBACKIEM —
    courier_resolver użyje go tylko gdy kurier NIE jest na realnej zmianie (nie rozszerza
    godzin powracającego, który jest w grafiku). Uczciwe potwierdzenie."""
    availability_contract_on = _availability_contract_enabled()
    # Identity jest precondition całej transakcji, nie walidacją po legacy
    # mutacji. Dwa historyczne resolvery muszą wskazać ten sam CID; dopóki
    # istnieją oba, rozjazd kończy się byte-preserving fail-closed.
    try:
        name_to_cid = _all_name_to_cid()
    except Exception:
        name_to_cid = {}
    _inc_cid = name_to_cid.get(courier)
    legacy_cid = _resolve_cid(courier)
    if availability_contract_on and (
        _inc_cid is None
        or legacy_cid == "?"
        or str(_inc_cid) != str(legacy_cid)
    ):
        return "include", (
            f"⚠️ {courier}: nie znam cid jednoznacznie — stan bez zmian. "
            "Użyj /dopisz <cid> <imię>"
        )

    from dispatch_v2 import courier_availability as _availability

    # Komenda niesie wyłącznie intencję. Nie publikujemy kopii ``data``:
    # owner store'u odczyta świeży snapshot już pod canonical lockiem.
    operator_at = datetime.now(timezone.utc)
    aliases = tuple(
        name
        for name, mapped_cid in name_to_cid.items()
        if _inc_cid is not None and mapped_cid == _inc_cid
    )
    mutation_cid = (
        str(_inc_cid)
        if availability_contract_on and _inc_cid is not None
        else legacy_cid
    )
    added = None
    working_entry = None
    operator_window = None
    if add_to_grafik:
        scratch = {"working": {}}
        added = _add_working(
            scratch,
            courier,
            text,
            at=operator_at,
            resolved_cid=(
                mutation_cid if mutation_cid != "?" else None
            ),
        )
        if added is None:
            return "include", (
                f"⚠️ {courier}: nie znam cid jednoznacznie — stan bez zmian. "
                "Użyj /dopisz <cid> <imię>"
            )
        working_entry = scratch["working"][added[0]]
        operator_window = {
            "start": working_entry.get("start"),
            "end": working_entry.get("end"),
            "end_explicit": working_entry.get("end_explicit"),
        }
        mutation = _availability.ConsoleAvailabilityMutation.on(
            added[0],
            courier,
            working_entry=working_entry,
            operator_window=operator_window,
            aliases=aliases,
            at=operator_at,
            project_operator=availability_contract_on,
        )
    else:
        mutation = _availability.ConsoleAvailabilityMutation.clear(
            mutation_cid,
            courier,
            aliases=aliases,
            at=operator_at,
            project_operator=availability_contract_on,
        )
    committed = _availability.commit_console_mutation(
        mutation,
        path=OVERRIDES_PATH,
    )
    before = committed.before_payload
    before_excluded = set(before.get("excluded", []) or [])
    before_excluded_cids = {
        str(value) for value in before.get("excluded_cids", []) or []
    }
    was_excluded = bool(
        before_excluded.intersection(set(mutation.aliases))
        or (
            mutation.cid is not None
            and mutation.cid in before_excluded_cids
        )
    )
    data.clear()
    data.update(committed.payload)
    if not committed.applied:
        return "include", (
            f"⚠️ {courier}: nowsza komenda dostępności już obowiązuje — "
            "starszy zapis został pominięty"
        )
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
    availability_contract_on = _availability_contract_enabled()
    cid = _resolve_cid(courier)
    try:
        canonical_cid = _all_name_to_cid().get(courier)
    except Exception:
        canonical_cid = None
    if availability_contract_on and (
        cid == "?"
        or canonical_cid is None
        or str(canonical_cid) != str(cid)
    ):
        return "exclude", (
            f"⚠️ {courier}: nie znam cid jednoznacznie — STOP nie został zapisany"
        )

    from dispatch_v2 import courier_availability as _availability

    mutation = _availability.ConsoleAvailabilityMutation.off(
        cid,
        courier,
        at=datetime.now(timezone.utc),
        project_operator=availability_contract_on,
    )
    committed = _availability.commit_console_mutation(
        mutation,
        path=OVERRIDES_PATH,
    )
    data.clear()
    data.update(committed.payload)
    if not committed.applied:
        return "exclude", (
            f"⚠️ {courier}: nowsza komenda dostępności już obowiązuje — "
            "starszy STOP został pominięty"
        )
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
        from dispatch_v2 import courier_availability as _availability

        _availability.reset_legacy_fields(path=OVERRIDES_PATH)
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
