"""Operator route-order override — pin kolejności podjazdów koordynatora w KANONIE.

Zadanie ownera 2026-07-19: koordynator ustawia w konsoli KOLEJNOŚĆ podjazdów
kuriera; Ziomek honoruje tę kolejność w kanonie (courier_plans.json) i PRZELICZA
czasy legów istniejącą maszynerią (`plan_recheck._retime_stops` — łańcuch OSRM
+ clamp committed). Konsola (`fleet_state._build_route` → `route_order.
order_podjazdy(trust_canon)`) i apka (`route_podjazdy`) czytają kanon — zmiana
jest dla nich przezroczysta.

KONTRAKT WEJŚCIA (CTO 2026-07-19, nie zmieniać bez raportu):
plik `operator_route_overrides.json` w katalogu żywego stanu silnika (ten sam,
w którym silnik czyta/pisze `orders_state.json`):
    {"courier_overrides": {"<cid>": {"order_ids": ["<zid>", ...],
        "set_by": "<email>", "set_at": "<ISO8601>", "ttl_min": 120}}}
`order_ids` = PEŁNA pożądana sekwencja obsługi zleceń AKTYWNEGO worka kuriera
(permutacja zbioru aktywnych zleceń — jedno wejście per zlecenie). Zbiór id
MUSI być identyczny ze zbiorem aktywnych zleceń kuriera w kanonie; inaczej
override jest IGNOROWANY (+ telemetria z powodem). Po `ttl_min` od `set_at`
override wygasa. Odczyt odporny na brak/uszkodzenie pliku — fail-open
(zachowanie dotychczasowe).

SEMANTYKA SEKWENCJI (mapowanie zlecenia→węzły planu): idziemy po `order_ids`
od lewej; KOLEJNE zlecenia z tej samej restauracji (oba z węzłem odbioru w
planie) = jeden podjazd (wszystkie odbiory grupy, potem wszystkie dostawy grupy
— w kolejności operatora); zlecenie niesione (bez węzła odbioru) = sama dostawa
na swojej pozycji. To odbicie 1:1 projekcji podjazdów `route_order.
_canon_order_from_plan` (scalanie sąsiednich odbiorów tej samej restauracji),
więc konsola po zapisie pokaże stopy dokładnie w kolejności operatora.
Pin przestawia WYŁĄCZNIE istniejące węzły planu (żaden nie ginie, żaden nie
powstaje) — multiset węzłów zachowany (tripwire w budowie).

HARD vs SOFT: pin jest NADRZĘDNY wobec soft-heurystyk kolejności (carried-first
floor/relax, no-return, lex-window — nakładany PO `_apply_canon_order_invariants`
w obu writerach), ale NIE dotyka zobowiązań: `czas_kuriera` (R27) pozostaje
nietykalny — clamp „odbiór nie wcześniej niż committed" w `_retime_stops` oraz
refloor działają bez zmian, a spóźnienie odbioru > tolerancji R27 wynikłe z
sekwencji operatora jest LOGOWANE (`committed_breaches` w zdarzeniu applied),
nie ciche (koordynator nadzoruje = jego decyzja).

FLAGA: `ENABLE_OPERATOR_ROUTE_ORDER_OVERRIDE` (decision_flag, default OFF,
hot-reload z flags.json jak inne flagi silnika). Telemetria działa ZAWSZE —
także przy fladze OFF logujemy wykrycie/walidację pliku (cień zbiera dane przed
flipem): `operator_route_override_events.jsonl` obok orders_state, zdarzenia
`operator_route_override_{applied|rejected|expired}` z cid, liczbą stopów i
powodem odrzucenia. Zdarzenia rejected/expired deduplikowane w procesie
(per cid+set_at+powód), applied logowane przy każdym ZAPISIE planu z pinem.

Punkty wpięcia (jedno źródło, warstwa 9 kanon/plan): `plan_recheck.
_gen_one_bag_plan` (świeża decyzja: tick, gap-fill, redecide) oraz
`plan_recheck._retime_one_bag_plan` (recanon-on-write: 4 handlery
panel_watcher assign/pickup/deliver/return + sequence-lock tick) — wszyscy
writerzy sekwencji kanonu przechodzą przez te dwie funkcje.
"""
from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

_log = logging.getLogger("dispatch.operator_route_override")

# Ścieżki jak w plan_recheck (żywy stan POZA repo). Testy monkeypatchują.
OVERRIDES_PATH = "/root/.openclaw/workspace/dispatch_state/operator_route_overrides.json"
EVENTS_PATH = "/root/.openclaw/workspace/dispatch_state/operator_route_override_events.jsonl"
_DEFAULT_OVERRIDES_PATH = OVERRIDES_PATH
_DEFAULT_EVENTS_PATH = EVENTS_PATH

FLAG_NAME = "ENABLE_OPERATOR_ROUTE_ORDER_OVERRIDE"
DEFAULT_TTL_MIN = 120.0
# Tolerancja logowania naruszenia okna committed (R27 soft ±5): odbiór
# przewidziany > committed + tol → wpis committed_breaches (log, nie zmiana).
COMMITTED_LATE_LOG_TOL_MIN = 5.0

# Dedup powtarzalnych zdarzeń nie-applied w obrębie procesu (tick co 5 min +
# recanon per zdarzenie → bez dedupu przeterminowany wpis spamowałby jsonl).
_EMITTED: set = set()
_EMITTED_MAX = 512

# v2 (NO-GO Sola 2026-07-19): set_at z przyszłości poza skew = invalid_set_at.
SET_AT_FUTURE_SKEW_MIN = 2.0

# Cache dokumentu po (path, mtime_ns, size): parse WYŁĄCZNIE przy zmianie pliku,
# brak pliku = pojedynczy os.stat (koszt OFF — Sol pkt 6). Corrupt też cache'owany
# (bez re-parse spamu aż do zmiany mtime). "parses" = licznik dowodowy do testu.
_DOC_CACHE: Dict[str, Any] = {"path": None, "sig": None, "doc": None,
                              "err": None, "parses": 0}


def _r27_alert_threshold_min() -> float:
    """Próg ALERT dla spóźnienia vs committed — wspólny z bramką BUG C
    (COMMIT_DIVERGENCE_VERDICT_KOORD_MIN_MIN, default 10.0)."""
    try:
        from dispatch_v2 import common as C
        return float(getattr(C, "COMMIT_DIVERGENCE_VERDICT_KOORD_MIN_MIN", 10.0))
    except Exception:
        return 10.0


def _under_pytest() -> bool:
    return bool(os.environ.get("DISPATCH_UNDER_PYTEST")
                or os.environ.get("PYTEST_CURRENT_TEST"))


def _parse_iso(s: Any) -> Optional[datetime]:
    if not s or not isinstance(s, str):
        return None
    try:
        d = datetime.fromisoformat(s.replace("Z", "+00:00"))
        return d.replace(tzinfo=timezone.utc) if d.tzinfo is None else d
    except Exception:
        return None


def _emit(kind: str, cid: str, now: datetime, *, dedup_key: Optional[tuple] = None,
          **fields: Any) -> None:
    """Append zdarzenia telemetrii (fail-soft; hermetycznie: pod pytestem nie
    pisze na domyślną żywą ścieżkę — test podpina własną przez monkeypatch)."""
    try:
        if _under_pytest() and EVENTS_PATH == _DEFAULT_EVENTS_PATH:
            return
        if dedup_key is not None:
            key = (kind, cid) + dedup_key
            if key in _EMITTED:
                return
            if len(_EMITTED) > _EMITTED_MAX:
                _EMITTED.clear()
            _EMITTED.add(key)
        rec = {"ts": now.astimezone(timezone.utc).isoformat(),
               "event": f"operator_route_override_{kind}", "cid": str(cid)}
        rec.update(fields)
        line = json.dumps(rec, ensure_ascii=False) + "\n"
        fd = os.open(EVENTS_PATH, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o644)
        try:
            os.write(fd, line.encode("utf-8"))
        finally:
            os.close(fd)
    except Exception:
        pass


def _flag_on() -> bool:
    try:
        from dispatch_v2 import common as C
        return bool(C.decision_flag(FLAG_NAME))
    except Exception:
        return False


def _load_doc() -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
    """(doc | None, błąd pliku | None) z cache po (mtime_ns, size). Brak pliku =
    pojedynczy stat i czysty (None, None)."""
    path = OVERRIDES_PATH
    try:
        st = os.stat(path)
    except FileNotFoundError:
        return None, None
    except Exception:
        return None, "file_corrupt"
    sig = (st.st_mtime_ns, st.st_size)
    if _DOC_CACHE["path"] == path and _DOC_CACHE["sig"] == sig:
        return _DOC_CACHE["doc"], _DOC_CACHE["err"]
    doc: Optional[Dict[str, Any]] = None
    err: Optional[str] = None
    _DOC_CACHE["parses"] += 1
    try:
        with open(path, encoding="utf-8") as fh:
            raw = json.load(fh)
        if isinstance(raw, dict) and isinstance(raw.get("courier_overrides"), dict):
            doc = raw
        else:
            err = "file_corrupt"
    except Exception:
        err = "file_corrupt"
    _DOC_CACHE.update({"path": path, "sig": sig, "doc": doc, "err": err})
    return doc, err


def _read_entry(cid: str) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
    """(wpis dla cid | None, powód błędu pliku | None). Brak pliku/wpisu = cicho.
    Uszkodzony plik = ("file_corrupt") — fail-open u callera."""
    if _under_pytest() and OVERRIDES_PATH == _DEFAULT_OVERRIDES_PATH:
        return None, None  # hermetyczność: testy nie czytają żywego stanu
    doc, err = _load_doc()
    if err is not None:
        return None, err
    if doc is None:
        return None, None
    entry = doc["courier_overrides"].get(str(cid))
    if entry is None:
        return None, None
    if not isinstance(entry, dict):
        return {"_raw": entry}, "malformed"
    return entry, None


_TTL_WARNED: set = set()


def _ttl_min(entry: Dict[str, Any]) -> float:
    """ttl_min z wpisu: WYŁĄCZNIE int w zakresie 1..1440 (kontrakt v4 — Sol r3:
    odrzucamy też "60" string i 60.0 float). Brak pola = default 120 (cicho);
    każda inna wartość → default 120 + log (raz per wartość w procesie)."""
    if "ttl_min" not in entry:
        return DEFAULT_TTL_MIN
    v = entry.get("ttl_min")
    if isinstance(v, int) and not isinstance(v, bool) and 1 <= v <= 1440:
        return float(v)
    key = repr(v)[:64]
    if key not in _TTL_WARNED:
        if len(_TTL_WARNED) > 128:
            _TTL_WARNED.clear()
        _TTL_WARNED.add(key)
        _log.warning("OPERATOR-OVERRIDE ttl_min=%s odrzucone (kontrakt: int "
                     "1..1440) → default %.0f", key, DEFAULT_TTL_MIN)
    return DEFAULT_TTL_MIN


def _iso_has_offset(s: Any) -> bool:
    """True gdy string ISO ma jawny offset ('Z' lub ±HH:MM) — kontrakt v3:
    set_at bez offsetu = invalid (nie zgadujemy strefy)."""
    if not isinstance(s, str) or not s:
        return False
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00")).tzinfo is not None
    except Exception:
        return False


def _group_key(oid: str, orders_state: Dict[str, Any],
               pickups: Dict[str, dict]) -> tuple:
    rec = orders_state.get(oid) or {}
    rest = rec.get("restaurant")
    if rest:
        return ("r", str(rest))
    c = (pickups.get(oid) or {}).get("coords") or {}
    try:
        return ("c", round(float(c.get("lat", 0.0)), 6),
                round(float(c.get("lng", 0.0)), 6))
    except Exception:
        return ("c", str(c))


def _build_pinned(stops: List[dict], order_ids: List[str],
                  orders_state: Dict[str, Any]) -> Optional[List[dict]]:
    """Przestaw ISTNIEJĄCE węzły planu w sekwencję operatora (podjazdy: kolejne
    zlecenia tej samej restauracji = odbiory grupą, potem dostawy grupą).
    None gdy struktura nie pozwala (duplikat węzła / brak dostawy / niezgodny
    multiset) — caller zostawia plan bez zmian (fail-open)."""
    pickups: Dict[str, dict] = {}
    drops: Dict[str, dict] = {}
    for s in stops:
        oid = str(s.get("order_id"))
        bucket = pickups if s.get("type") == "pickup" else drops
        if oid in bucket:
            return None  # duplikat węzła tego samego typu — nie dotykamy
        bucket[oid] = s
    for oid in order_ids:
        if oid not in drops:
            return None  # każde aktywne zlecenie musi mieć węzeł dostawy
    out: List[dict] = []
    i = 0
    n = len(order_ids)
    while i < n:
        oid = order_ids[i]
        if oid not in pickups:
            out.append(drops[oid])  # niesione: sama dostawa na pozycji operatora
            i += 1
            continue
        key = _group_key(oid, orders_state, pickups)
        grp = [oid]
        j = i + 1
        while j < n and order_ids[j] in pickups and \
                _group_key(order_ids[j], orders_state, pickups) == key:
            grp.append(order_ids[j])
            j += 1
        out.extend(pickups[g] for g in grp)
        out.extend(drops[g] for g in grp)
        i = j
    if len(out) != len(stops):
        return None  # tripwire: multiset węzłów MUSI być zachowany
    return out


def pin_stops(cid: str, stops: List[dict], oids: List[str],
              orders_state: Dict[str, Any],
              now: Optional[datetime] = None
              ) -> Tuple[List[dict], Optional[Dict[str, Any]]]:
    """Nałóż (gdy flaga ON i override ważny) sekwencję operatora na stops.

    Zwraca (stops', ctx|None). ctx != None ⇔ pin AKTYWNY (caller po udanym
    zapisie planu woła emit_applied). Każda inna ścieżka = fail-open: stops
    bez zmian + ewentualna telemetria rejected/expired. Nigdy nie rzuca.
    """
    cid = str(cid)
    if now is None:
        now = datetime.now(timezone.utc)
    try:
        # Flaga PRZED jakimkolwiek I/O pliku (koszt OFF — Sol pkt 6; load_flags
        # ma własny cache mtime/TTL). Odczyt pliku niżej: brak = 1 stat, obecny
        # = parse tylko przy zmianie mtime (_load_doc). Cień would_apply przy
        # OFF zostaje (wymóg E briefu) — kosztuje stat, nie parse.
        flag_on = _flag_on()
        entry, err = _read_entry(cid)
        if entry is None and err is None:
            return stops, None  # brak pliku/wpisu — zero szumu, koszt 1 stat
        base = {"stops": len(stops), "flag_on": flag_on}
        if err == "file_corrupt":
            _emit("rejected", cid, now, dedup_key=("file_corrupt",),
                  reason="file_corrupt", **base)
            return stops, None
        set_at = _parse_iso((entry or {}).get("set_at"))
        set_by = (entry or {}).get("set_by")
        base.update({"set_by": set_by, "set_at": (entry or {}).get("set_at")})
        order_ids = (entry or {}).get("order_ids")
        if err == "malformed" or set_at is None or not isinstance(order_ids, list) \
                or not all(isinstance(x, str) and x for x in order_ids):
            _emit("rejected", cid, now,
                  dedup_key=("malformed", str((entry or {}).get("set_at"))),
                  reason="malformed", **base)
            return stops, None
        if not _iso_has_offset((entry or {}).get("set_at")):
            # kontrakt: ISO z jawnym offsetem — naiwny czas = nie zgadujemy strefy
            _emit("rejected", cid, now,
                  dedup_key=("invalid_set_at_naive", entry.get("set_at")),
                  reason="invalid_set_at", **base)
            return stops, None
        if (set_at - now) > timedelta(minutes=SET_AT_FUTURE_SKEW_MIN):
            # set_at z przyszłości poza skew zegarów = wpis niewiarygodny
            _emit("rejected", cid, now,
                  dedup_key=("invalid_set_at", entry.get("set_at")),
                  reason="invalid_set_at", **base)
            return stops, None
        ttl = _ttl_min(entry)
        age_min = (now - set_at).total_seconds() / 60.0
        if age_min > ttl:
            _emit("expired", cid, now, dedup_key=("expired", entry.get("set_at")),
                  ttl_min=ttl, age_min=round(age_min, 1), **base)
            return stops, None
        order_ids = [str(x) for x in order_ids]
        if len(set(order_ids)) != len(order_ids):
            _emit("rejected", cid, now,
                  dedup_key=("duplicate_ids", entry.get("set_at")),
                  reason="duplicate_ids", **base)
            return stops, None
        if set(order_ids) != {str(o) for o in oids}:
            _emit("rejected", cid, now,
                  dedup_key=("set_mismatch", entry.get("set_at"),
                             tuple(sorted(str(o) for o in oids))),
                  reason="set_mismatch",
                  override_ids=sorted(order_ids),
                  active_ids=sorted(str(o) for o in oids), **base)
            return stops, None
        stop_oids = {str(s.get("order_id")) for s in stops}
        if not stop_oids <= set(order_ids):
            _emit("rejected", cid, now,
                  dedup_key=("foreign_stops", entry.get("set_at")),
                  reason="foreign_stops", **base)
            return stops, None
        # v3: dry-run KONSTRUKCJI przed werdyktem flagi — strukturalnie
        # niemożliwy override nie może fałszywie przejść cienia would_apply.
        pinned = _build_pinned(stops, order_ids, orders_state)
        if pinned is None:
            _emit("rejected", cid, now,
                  dedup_key=("structure_fail", entry.get("set_at")),
                  reason="structure_fail", **base)
            return stops, None
        if not flag_on:
            # Cień PRZED flipem: wpis przeszedł PEŁNĄ walidację + konstrukcję.
            _emit("rejected", cid, now, dedup_key=("flag_off", entry.get("set_at")),
                  reason="flag_off", would_apply=True, **base)
            return stops, None
        changed = ([(s.get("type"), str(s.get("order_id"))) for s in pinned]
                   != [(s.get("type"), str(s.get("order_id"))) for s in stops])
        ctx = {"set_by": set_by, "set_at": entry.get("set_at"), "ttl_min": ttl,
               "changed": changed, "order_ids": order_ids}
        return pinned, ctx
    except Exception:
        return stops, None  # fail-open zawsze


def emit_applied(cid: str, ctx: Dict[str, Any], final_stops: List[dict],
                 orders_state: Dict[str, Any], now: Optional[datetime] = None,
                 hard_breaches: Optional[List[dict]] = None,
                 l3: Optional[Dict[str, Any]] = None) -> None:
    """Po UDANYM zapisie planu z pinem: jedno zdarzenie applied z finalnymi
    czasami + naruszenia okna committed (odbiór > czas_kuriera + tol; wpis z
    late > progu BUG C dostaje `alert` i WARNING rangi ALERT) + wynik ewaluacji
    HARD po pinie (`hard_breaches` z plan_recheck: r6/no_return). Polityka
    przypięta (CTO 2026-07-19): sekwencję WYKONUJEMY (koordynator nadzoruje),
    naruszenia raportujemy GŁOŚNO — zobowiązanie czas_kuriera NIEZMIENIONE,
    zero veta poza technicznym (retime_failed)."""
    if now is None:
        now = datetime.now(timezone.utc)
    try:
        thr = _r27_alert_threshold_min()
        breaches = []
        r27_alert = False
        for s in final_stops:
            if s.get("type") != "pickup":
                continue
            oid = str(s.get("order_id"))
            ck = _parse_iso((orders_state.get(oid) or {}).get("czas_kuriera_warsaw"))
            pred = _parse_iso(s.get("predicted_at"))
            if ck is None or pred is None:
                continue
            late_min = (pred - ck).total_seconds() / 60.0
            if late_min > COMMITTED_LATE_LOG_TOL_MIN:
                b = {"oid": oid, "late_min": round(late_min, 1),
                     "alert": bool(late_min > thr)}
                r27_alert = r27_alert or b["alert"]
                breaches.append(b)
        hb = list(hard_breaches or [])
        if r27_alert:
            _log.warning(
                "OPERATOR-OVERRIDE R27 ALERT cid=%s late>%.0fmin breaches=%s — "
                "zobowiązanie NIEZMIENIONE, sekwencja wykonana (koordynator "
                "poinformowany przez badge/event)", cid, thr,
                [b for b in breaches if b.get("alert")])
        _emit("applied", str(cid), now, stops=len(final_stops), flag_on=True,
              set_by=ctx.get("set_by"), set_at=ctx.get("set_at"),
              ttl_min=ctx.get("ttl_min"), changed=bool(ctx.get("changed")),
              committed_breaches=breaches, r27_alert=r27_alert,
              hard_breaches=hb,
              l3_would_reject=bool(l3 and l3.get("l3_would_reject")),
              l3_detail=(l3 or None))
    except Exception:
        pass


def emit_retime_failed(cid: str, ctx: Optional[Dict[str, Any]], stops_count: int,
                       now: Optional[datetime] = None) -> None:
    """VETO TECHNICZNE (jedyne dozwolone politykę przypiętą): pin przestawił
    stopy, ale czasów nowej sekwencji nie dało się przeliczyć (OSRM/coords) →
    zapis PRZERWANY, poprzedni plan nietknięty. Bez dedupu — każda odmowa ma
    być widoczna w cieniu (powtórki = czas trwania awarii OSRM)."""
    if now is None:
        now = datetime.now(timezone.utc)
    try:
        _emit("rejected", str(cid), now, reason="retime_failed",
              stops=int(stops_count), flag_on=True,
              set_by=(ctx or {}).get("set_by"), set_at=(ctx or {}).get("set_at"))
    except Exception:
        pass
