#!/usr/bin/env python3
"""Tablica zdrowia + budżet błędu (SLO) — READ-ONLY agregator (Sprint E, 2026-07-08).

JEDEN pulpit prawdy o żywych flipach/cieniach Ziomka. Czyta 6 istniejących logów
(NIE modyfikuje żadnego kolektora ani silnika), liczy budżet błędu / SLO-burn per
metryka i wypisuje dzienną kartę 🟢/🟡/🔴 + „co wymaga uwagi Adriana".

CZYSTY KONSUMENT: jedyna zmiana stanu, jaką robi ten moduł, to zapis WŁASNEJ karty
do `dispatch_state/health_scoreboard_card.{md,json}`. Zero mutacji cudzych logów,
zero wpływu na decyzje. To gwarantuje bezkolizyjność z równoległymi sprintami
(C route-order / D pipeline / A solver / B feasibility+claim-ledger / cień ETA).

Źródła (E1):
  1. shadow_decisions.jsonl     — decyzje: KOORD-rate, redirecty, best_effort, latency, pula feasible
  2. eta_calib_metrics.jsonl    — on-time odbiór/dostawa (ONTIME_operacyjna), MAE, spoznien_pct  [CIEŃ]
  3. pending_global_resweep.jsonl — g_claim_ledger_breaches, would_repropose, no_courier
  4. proposal_churn.log         — migotanie top-1 (%≥1 / %≥3 / flick_same%) — raport TEKSTOWY
  5. night_guard_history.jsonl  — nocny pytest (failed), entropia (poison_live/instr), verdict
  6. pickup_slip_monitor.jsonl  — poślizg odbioru (median optymizmu, per segment obciążenia)

BUDŻET BŁĘDU (E2): dla metryk z twardym/danymi-wyprowadzonym celem liczymy SLO-burn:
  • cel „≥ X%" (on-time): budżet = 100−X; zjedzone = 100−actual; burn = zjedzone/budżet.
  • cel „== 0" (breaches, pytest.failed, no_courier): 0 → 0% burn 🟢; ≥1 → naruszenie 🔴.
  • cel „≤ C" (KOORD-rate, latency p95, flicker): burn = actual/C.
  Kolory: 🟢 burn <75% · 🟡 75–100% · 🔴 >100% (budżet przekroczony).
  Progi PROWIZORYCZNE (heurystyka) są jawnie oznaczone — do potwierdzenia przez Adriana.

Uczciwość (watchpoint handoffu): gdzie okno bez danych / rekord nieświeży → status
„⚪ za mało danych", NIGDY zmyślona „poprawa" ani istotność statystyczna.

Uruchom (READ-ONLY):
  python3 tools/health_scoreboard.py [--window-hours 24] [--out-dir DIR] [--stdout] [--asof ISO]

Timer (PROPOZYCJA, NIE instalować bez ACK) — patrz komentarz na końcu pliku.
"""
from __future__ import annotations

import argparse
import glob
import gzip
import json
import os
import re
import sys
import tempfile
from datetime import datetime, timedelta, timezone

# ─────────────────────────────────────────────────────────────────────────────
# Domyślne ścieżki źródeł (READ-ONLY) + katalog wyjścia własnej karty.
# ─────────────────────────────────────────────────────────────────────────────
LOGS_DIR = "/root/.openclaw/workspace/scripts/logs"
STATE_DIR = "/root/.openclaw/workspace/dispatch_state"

SRC_SHADOW = os.path.join(LOGS_DIR, "shadow_decisions.jsonl")
SRC_ETA_CALIB = os.path.join(STATE_DIR, "eta_calib_metrics.jsonl")
SRC_RESWEEP = os.path.join(STATE_DIR, "pending_global_resweep.jsonl")
SRC_CHURN = os.path.join(LOGS_DIR, "proposal_churn.log")
SRC_NIGHT_GUARD = os.path.join(STATE_DIR, "night_guard_history.jsonl")
SRC_PICKUP_SLIP = os.path.join(STATE_DIR, "pickup_slip_monitor.jsonl")

OUT_DIR_DEFAULT = STATE_DIR
CARD_MD = "health_scoreboard_card.md"
CARD_JSON = "health_scoreboard_card.json"

# Bajtowy ogon na plik przy skanie dużych jsonl (shadow ~70MB, rec ~90KB).
DEFAULT_MAX_SCAN_BYTES = 220 * 1024 * 1024

GREEN, YELLOW, RED, GREY = "🟢", "🟡", "🔴", "⚪"

# ─────────────────────────────────────────────────────────────────────────────
# Parsowanie znaczników czasu (aware UTC). Loguje różne formaty:
#  - ISO z offsetem "+00:00" / "+02:00"
#  - ISO naive (pickup_slip: "2026-07-07T22:30:03.4") → traktuj jako UTC (best-effort;
#    użyty tylko do świeżości, nie do porównań między-strefowych).
# ─────────────────────────────────────────────────────────────────────────────
def parse_ts(value):
    if not value or not isinstance(value, str):
        return None
    s = value.strip().replace("Z", "+00:00")
    try:
        dtv = datetime.fromisoformat(s)
    except ValueError:
        return None
    if dtv.tzinfo is None:
        dtv = dtv.replace(tzinfo=timezone.utc)
    return dtv.astimezone(timezone.utc)


def _percentile(sorted_vals, q):
    """q w [0,1]; sorted_vals niepusta lista posortowana rosnąco."""
    if not sorted_vals:
        return None
    idx = int(round(q * (len(sorted_vals) - 1)))
    idx = max(0, min(len(sorted_vals) - 1, idx))
    return sorted_vals[idx]


def _open_maybe_gz(path):
    if path.endswith(".gz"):
        return gzip.open(path, "rt", encoding="utf-8", errors="replace")
    return open(path, "r", encoding="utf-8", errors="replace")


def _rotated_siblings(path):
    """[live, .1, .2, ...] w kolejności NAJNOWSZY→NAJSTARSZY (logrotate).

    Zwraca istniejące pliki: sam `path`, potem `path.1`/`path.1.gz`, `path.2`… .
    Rotation-aware odczyt (C16 / L1.2): brak tego = undercount po rotacji.
    """
    files = []
    if os.path.exists(path):
        files.append(path)
    n = 1
    while n < 20:
        cand = None
        for suf in (f".{n}", f".{n}.gz"):
            if os.path.exists(path + suf):
                cand = path + suf
                break
        if cand is None:
            break
        files.append(cand)
        n += 1
    return files


def iter_jsonl_since(path, since, max_scan_bytes=DEFAULT_MAX_SCAN_BYTES):
    """Strumieniowo yielduje sparsowane rekordy jsonl z `path` (+ rotowane siblingi),
    z ts >= `since` (aware UTC). Bajtowy ogon ogranicza I/O na dużych plikach.

    NIE ładuje całego pliku do pamięci — parsuje linię po linii. Gdy `since` jest
    None → cała dostępna zawartość (w granicach max_scan_bytes na plik).
    """
    for fpath in _rotated_siblings(path):
        earliest_in_file = None
        try:
            size = os.path.getsize(fpath)
        except OSError:
            continue
        seek_from_start = True
        # Bajtowy ogon tylko dla niekompresowanych (gz trzeba czytać w całości).
        if not fpath.endswith(".gz") and size > max_scan_bytes:
            seek_from_start = False
        try:
            if fpath.endswith(".gz"):
                fh = _open_maybe_gz(fpath)
                skip_first = False
            else:
                fh = open(fpath, "r", encoding="utf-8", errors="replace")
                if not seek_from_start:
                    fh.seek(size - max_scan_bytes)
                    skip_first = True  # pierwsza linia prawdopodobnie ucięta
                else:
                    skip_first = False
        except OSError:
            continue
        with fh:
            first = True
            for line in fh:
                if first and skip_first:
                    first = False
                    continue
                first = False
                line = line.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                except (ValueError, TypeError):
                    continue
                ts = parse_ts(obj.get("ts") or obj.get("logged_at"))
                if ts is not None:
                    if earliest_in_file is None or ts < earliest_in_file:
                        earliest_in_file = ts
                    if since is not None and ts < since:
                        continue
                yield obj
        # Zejdź do starszego siblinga tylko, jeśli TEN pokrył okno w całości
        # (przeczytany od początku) a i tak jego najstarszy rekord > since.
        if since is not None and seek_from_start and earliest_in_file is not None:
            if earliest_in_file <= since:
                break  # okno domknięte, starsze pliki zbędne
        elif since is not None and not seek_from_start:
            # czytaliśmy tylko ogon — nie wiemy czy starszy plik potrzebny; zejdź niżej
            # tylko jeśli ogon nie sięgnął cutoffu (earliest_in_file > since).
            if earliest_in_file is not None and earliest_in_file <= since:
                break


# ─────────────────────────────────────────────────────────────────────────────
# LOADERY (każdy zwraca dict; klucz "data_ok"=False → za mało danych).
# ─────────────────────────────────────────────────────────────────────────────
_REDIRECT_KEYS = (
    "best_effort_r6_redirect",
    "commit_divergence_redirect",
    "pickup_extension_redirect",
    "difficult_case_redirect",
)


def load_shadow(path, since, max_scan_bytes=DEFAULT_MAX_SCAN_BYTES):
    n = 0
    verdict = {}
    auto_route = {}
    koord = 0
    best_effort = 0
    feas0 = 0
    redirects = {k: 0 for k in _REDIRECT_KEYS}
    lat = []
    ts_min = None
    ts_max = None
    for o in iter_jsonl_since(path, since, max_scan_bytes):
        n += 1
        v = o.get("verdict")
        verdict[v] = verdict.get(v, 0) + 1
        ar = o.get("auto_route")
        auto_route[ar] = auto_route.get(ar, 0) + 1
        if v == "KOORD" or ar == "KOORD":
            koord += 1
        best = o.get("best") or {}
        if isinstance(best, dict) and best.get("best_effort"):
            best_effort += 1
        if (o.get("pool_feasible_count") or 0) == 0:
            feas0 += 1
        for k in _REDIRECT_KEYS:
            if o.get(k):
                redirects[k] += 1
        lm = o.get("latency_ms")
        if isinstance(lm, (int, float)):
            lat.append(float(lm))
        ts = parse_ts(o.get("ts"))
        if ts is not None:
            ts_min = ts if ts_min is None or ts < ts_min else ts_min
            ts_max = ts if ts_max is None or ts > ts_max else ts_max
    lat.sort()
    return {
        "data_ok": n > 0,
        "n": n,
        "verdict": verdict,
        "auto_route": auto_route,
        "koord": koord,
        "koord_pct": (100.0 * koord / n) if n else None,
        "best_effort": best_effort,
        "best_effort_pct": (100.0 * best_effort / n) if n else None,
        "feas0": feas0,
        "feas0_pct": (100.0 * feas0 / n) if n else None,
        "redirects": redirects,
        "lat_n": len(lat),
        "lat_p50": _percentile(lat, 0.50),
        "lat_p95": _percentile(lat, 0.95),
        "lat_max": lat[-1] if lat else None,
        "ts_min": ts_min.isoformat() if ts_min else None,
        "ts_max": ts_max.isoformat() if ts_max else None,
    }


def _read_jsonl_all(path):
    """Cały mały jsonl (+ rotowane) jako lista rekordów, w kolejności pliku."""
    recs = []
    for fpath in reversed(_rotated_siblings(path)):  # najstarszy → najnowszy
        try:
            with _open_maybe_gz(fpath) as fh:
                for line in fh:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        recs.append(json.loads(line))
                    except (ValueError, TypeError):
                        continue
        except OSError:
            continue
    return recs


def load_eta_calib(path):
    recs = _read_jsonl_all(path)
    if not recs:
        return {"data_ok": False}
    last = recs[-1]
    legs = last.get("legs") or {}

    def _leg(name):
        leg = legs.get(name) or {}
        cov = leg.get("coverage") or {}
        return {
            "ontime": cov.get("ONTIME_operacyjna"),
            "spoznien_pct": cov.get("spoznien_pct"),
            "target_ontime_pct": (cov.get("target_ontime") * 100.0)
            if isinstance(cov.get("target_ontime"), (int, float))
            else None,
            "mae": leg.get("champion_mae"),
            "n_holdout": leg.get("n_holdout"),
            "champion": leg.get("champion"),
        }

    return {
        "data_ok": True,
        "logged_at": last.get("logged_at"),
        "promoted": last.get("promoted"),
        "holdout_cut_day": last.get("holdout_cut_day"),
        "pickup": _leg("pickup"),
        "delivery": _leg("delivery"),
        "series": [
            {
                "logged_at": r.get("logged_at"),
                "pickup_ontime": ((r.get("legs") or {}).get("pickup") or {}).get(
                    "coverage", {}
                ).get("ONTIME_operacyjna"),
                "delivery_ontime": ((r.get("legs") or {}).get("delivery") or {}).get(
                    "coverage", {}
                ).get("ONTIME_operacyjna"),
            }
            for r in recs
        ],
    }


def load_resweep(path, since, max_scan_bytes=DEFAULT_MAX_SCAN_BYTES):
    n = 0
    repropose = 0
    no_courier = 0
    breaches_sum = 0
    breaches_recs = 0
    reasons = {}
    ts_min = None
    ts_max = None
    for o in iter_jsonl_since(path, since, max_scan_bytes):
        n += 1
        if o.get("would_repropose"):
            repropose += 1
        if o.get("no_courier"):
            no_courier += 1
        b = o.get("g_claim_ledger_breaches") or 0
        try:
            b = int(b)
        except (ValueError, TypeError):
            b = 0
        breaches_sum += b
        if b:
            breaches_recs += 1
        r = o.get("reason")
        reasons[r] = reasons.get(r, 0) + 1
        ts = parse_ts(o.get("ts"))
        if ts is not None:
            ts_min = ts if ts_min is None or ts < ts_min else ts_min
            ts_max = ts if ts_max is None or ts > ts_max else ts_max
    return {
        "data_ok": n > 0,
        "n": n,
        "repropose": repropose,
        "repropose_pct": (100.0 * repropose / n) if n else None,
        "no_courier": no_courier,
        "no_courier_pct": (100.0 * no_courier / n) if n else None,
        "breaches_sum": breaches_sum,
        "breaches_recs": breaches_recs,
        "reasons": reasons,
        "ts_min": ts_min.isoformat() if ts_min else None,
        "ts_max": ts_max.isoformat() if ts_max else None,
    }


# Wiersz per-doba raportu churnu: "YYYY-MM-DD  N  ≥1%  ≥3%  śr  flick_same%"
_CHURN_ROW = re.compile(
    r"^(?P<day>\d{4}-\d{2}-\d{2})\s+(?P<n>\d+)\s+"
    r"(?P<ge1>[\d.]+)%\s+(?P<ge3>[\d.]+)%\s+"
    r"(?P<mean>[\d.]+)\s+(?P<flick>[\d.]+)%\s*$"
)


def load_churn(path):
    """Parsuje wiersze per-doba z TEKSTOWEGO raportu. Duplikaty dat (wiele przebiegów
    dopisanych do pliku) → wygrywa OSTATNIE wystąpienie (najświeższy przelicznik)."""
    by_day = {}
    order = []
    for fpath in _rotated_siblings(path):
        try:
            with _open_maybe_gz(fpath) as fh:
                for line in fh:
                    m = _CHURN_ROW.match(line)
                    if not m:
                        continue
                    day = m.group("day")
                    if day not in by_day:
                        order.append(day)
                    by_day[day] = {
                        "day": day,
                        "n": int(m.group("n")),
                        "ge1_pct": float(m.group("ge1")),
                        "ge3_pct": float(m.group("ge3")),
                        "mean_changes": float(m.group("mean")),
                        "flick_same_pct": float(m.group("flick")),
                    }
        except OSError:
            continue
    if not by_day:
        return {"data_ok": False}
    days = sorted(by_day)
    series = [by_day[d] for d in days]
    return {"data_ok": True, "last": series[-1], "series": series}


def load_night_guard(path):
    recs = _read_jsonl_all(path)
    if not recs:
        return {"data_ok": False}
    last = recs[-1]
    prev = recs[-2] if len(recs) >= 2 else None
    pytestd = last.get("pytest") or {}
    ent = last.get("entropy") or {}
    return {
        "data_ok": True,
        "ts": last.get("ts"),
        "verdict": last.get("verdict"),
        "pytest_failed": pytestd.get("failed"),
        "pytest_passed": pytestd.get("passed"),
        "entropy": ent,
        "alerts": last.get("alerts") or [],
        "prev_entropy": (prev or {}).get("entropy") if prev else None,
        "series": [
            {
                "ts": r.get("ts"),
                "failed": (r.get("pytest") or {}).get("failed"),
                "verdict": r.get("verdict"),
                "poison_live": (r.get("entropy") or {}).get("poison_live"),
                "poison_instr": (r.get("entropy") or {}).get("poison_instr"),
            }
            for r in recs
        ],
    }


def load_pickup_slip(path):
    recs = _read_jsonl_all(path)
    if not recs:
        return {"data_ok": False}
    last = recs[-1]
    seg = last.get("segments") or {}

    # Agreguj medianę optymizmu solo/bundle ważoną n (tylko segmenty z n>0).
    def _weighted(kind):
        num = 0.0
        den = 0
        for load_seg in seg.values():
            cell = (load_seg or {}).get(kind) or {}
            nn = cell.get("n") or 0
            med = cell.get("median")
            if nn and isinstance(med, (int, float)):
                num += med * nn
                den += nn
        return (num / den) if den else None, den

    solo_med, solo_n = _weighted("solo")
    bundle_med, bundle_n = _weighted("bundle")
    return {
        "data_ok": True,
        "ts": last.get("ts"),
        "window_days": last.get("window_days"),
        "n_total": last.get("n_total"),
        "solo_median": solo_med,
        "solo_n": solo_n,
        "bundle_median": bundle_med,
        "bundle_n": bundle_n,
        "segments": seg,
        "series": [
            {"ts": r.get("ts"), "n_total": r.get("n_total")} for r in recs
        ],
    }


# ─────────────────────────────────────────────────────────────────────────────
# SLO / budżet błędu.
# ─────────────────────────────────────────────────────────────────────────────
def slo_burn(actual, target, direction):
    """Zwraca (burn_pct, color). direction:
      'ge'   — cel „≥ target%" (on-time): burn = (100−actual)/(100−target).
      'le'   — cel „≤ target" (ceiling): burn = actual/target.
      'zero' — cel „== 0": 0→0%🟢, >0→naruszenie 🔴.
    burn wyrażony w % budżetu (100% = budżet w pełni zjedzony).
    """
    if actual is None or target is None:
        return None, GREY
    if direction == "zero":
        if actual <= 0:
            return 0.0, GREEN
        return None, RED  # naruszenie twardego inwariantu
    if direction == "ge":
        budget = 100.0 - target
        if budget <= 0:
            return (0.0, GREEN) if actual >= target else (None, RED)
        burn = 100.0 * (100.0 - actual) / budget
    elif direction == "le":
        if target <= 0:
            return (0.0, GREEN) if actual <= 0 else (None, RED)
        burn = 100.0 * actual / target
    else:
        return None, GREY
    if burn < 75.0:
        color = GREEN
    elif burn <= 100.0:
        color = YELLOW
    else:
        color = RED
    return burn, color


def _fmt(x, nd=1, suffix=""):
    if x is None:
        return "—"
    if isinstance(x, float):
        return f"{x:.{nd}f}{suffix}"
    return f"{x}{suffix}"


def build_card(window_hours, now, sources):
    """Zbiera wszystkie metryki w jednolitą listę wpisów + listę „uwaga Adriana"."""
    sh = sources["shadow"]
    eta = sources["eta_calib"]
    rs = sources["resweep"]
    ch = sources["churn"]
    ng = sources["night_guard"]
    ps = sources["pickup_slip"]

    metrics = []
    attention = []

    def add(key, label, source, status, burn, value, slo_txt, confidence, note=""):
        entry = {
            "key": key,
            "label": label,
            "source": source,
            "status": status,
            "burn_pct": round(burn, 1) if isinstance(burn, (int, float)) else None,
            "value": value,
            "slo": slo_txt,
            "confidence": confidence,  # twarda / prowizoryczna / informacyjna
            "note": note,
        }
        metrics.append(entry)
        if status == RED:
            attention.append((key, label, "🔴 " + (note or slo_txt)))
        return entry

    # M5 — claim-ledger breaches (TWARDY inwariant == 0). Sprint B.
    if rs.get("data_ok"):
        b = rs["breaches_sum"]
        burn, color = slo_burn(b, 0, "zero")
        add(
            "claim_ledger_breaches",
            "Claim-ledger breaches (double-book)",
            "pending_global_resweep",
            color,
            burn,
            f"suma={b} w {rs['n']} rek. (rekordów z breach={rs['breaches_recs']})",
            "cel = 0 (twardy inwariant no-double-book, Sprint B)",
            "twarda",
            note="≥1 breach = naruszenie inwariantu claim-ledger" if b else "",
        )
    else:
        add("claim_ledger_breaches", "Claim-ledger breaches", "pending_global_resweep",
            GREY, None, "za mało danych", "cel = 0", "twarda", "brak rekordów w oknie")

    # M6 — always-propose: no_courier == 0 (TWARDE).
    if rs.get("data_ok"):
        nc = rs["no_courier"]
        burn, color = slo_burn(nc, 0, "zero")
        add(
            "no_courier",
            'BRAK KANDYDATÓW (no_courier)',
            "pending_global_resweep",
            color,
            burn,
            f"{nc}/{rs['n']} ({_fmt(rs['no_courier_pct'],1,'%')})",
            'cel = 0 (always-propose: Ziomek nigdy „brak kandydatów”)',
            "twarda",
            note="wykryto no_courier — narusza always-propose" if nc else "",
        )

    # M8a — night guard: pytest.failed == 0 (TWARDE).
    if ng.get("data_ok"):
        f = ng.get("pytest_failed")
        if isinstance(f, int):
            burn, color = slo_burn(f, 0, "zero")
            stale = _is_stale(ng.get("ts"), now, hours=40)
            note = ""
            if f:
                note = f"nocny pytest: {f} failed (verdict={ng.get('verdict')})"
            if stale:
                note = (note + " ⚠ rekord nieświeży").strip()
            add(
                "night_pytest",
                "Nocny pytest (regresja)",
                "night_guard_history",
                color if not stale else (YELLOW if color == GREEN else color),
                burn,
                f"failed={f} passed={ng.get('pytest_passed')} verdict={ng.get('verdict')}",
                "cel = 0 failed (pełna regresja nocna)",
                "twarda",
                note=note,
            )

    # M3 — ETA on-time ODBIÓR (≥ target z danych, zwykle 80%). CIEŃ.
    if eta.get("data_ok"):
        pk = eta["pickup"]
        tgt = pk.get("target_ontime_pct") or 80.0
        stale = _is_stale(eta.get("logged_at"), now, hours=48)
        burn, color = slo_burn(pk.get("ontime"), tgt, "ge")
        add(
            "eta_ontime_pickup",
            "ETA on-time ODBIÓR (cień)",
            "eta_calib_metrics",
            color,
            burn,
            f"{_fmt(pk.get('ontime'),1,'%')} (cel ≥{_fmt(tgt,0,'%')}, spóźnień {_fmt(pk.get('spoznien_pct'),1,'%')}, MAE {_fmt(pk.get('mae'),2)} min, n={pk.get('n_holdout')})",
            f"on-time ≥ {_fmt(tgt,0)}% (cel z danych kalibracji)",
            "twarda",
            note="⚠ rekord kalibracji nieświeży (>48h)" if stale else "",
        )
        dv = eta["delivery"]
        tgt_d = dv.get("target_ontime_pct") or 80.0
        burn_d, color_d = slo_burn(dv.get("ontime"), tgt_d, "ge")
        add(
            "eta_ontime_delivery",
            "ETA on-time DOSTAWA (cień)",
            "eta_calib_metrics",
            color_d,
            burn_d,
            f"{_fmt(dv.get('ontime'),1,'%')} (cel ≥{_fmt(tgt_d,0,'%')}, spóźnień {_fmt(dv.get('spoznien_pct'),1,'%')}, MAE {_fmt(dv.get('mae'),2)} min, n={dv.get('n_holdout')})",
            f"on-time ≥ {_fmt(tgt_d,0)}% (cel z danych kalibracji)",
            "twarda",
            note="⚠ rekord kalibracji nieświeży (>48h); metryka w CIENIU (flaga OFF)" if stale else "metryka w CIENIU (flaga ETA OFF)",
        )
    else:
        add("eta_ontime_pickup", "ETA on-time ODBIÓR (cień)", "eta_calib_metrics",
            GREY, None, "za mało danych", "on-time ≥80%", "twarda", "brak rekordów")

    # M1 — KOORD-rate (≤ ceiling, PROWIZORYCZNY 10%).
    if sh.get("data_ok"):
        if sh["n"] >= 20:
            burn, color = slo_burn(sh["koord_pct"], 10.0, "le")
            add(
                "koord_rate",
                "KOORD-rate (eskalacja do koordynatora)",
                "shadow_decisions",
                color,
                burn,
                f"{sh['koord']}/{sh['n']} ({_fmt(sh['koord_pct'],1,'%')}); best_effort {_fmt(sh['best_effort_pct'],1,'%')}; pula feas=0 {_fmt(sh['feas0_pct'],1,'%')}",
                "KOORD-rate ≤ 10% (próg PROWIZORYCZNY — ACK Adriana)",
                "prowizoryczna",
                note="KOORD legalny dla early-bird/czasówka ≥60min — próg śledzi TREND, nie regułę",
            )
        else:
            add("koord_rate", "KOORD-rate", "shadow_decisions", GREY, None,
                f"za mało danych (n={sh['n']}<20)", "KOORD-rate ≤10%", "prowizoryczna",
                "okno za rzadkie na wiarygodny odsetek")

    # M2 — latency p95 (≤ ceiling; SHADOW, sprint A perf w toku).
    if sh.get("data_ok") and sh.get("lat_n", 0) >= 20:
        burn, color = slo_burn(sh["lat_p95"], 2500.0, "le")
        add(
            "latency_p95",
            "Latencja decyzji p95 (shadow)",
            "shadow_decisions",
            color,
            burn,
            f"p95={_fmt(sh['lat_p95'],0,'ms')} p50={_fmt(sh['lat_p50'],0,'ms')} max={_fmt(sh['lat_max'],0,'ms')} (n={sh['lat_n']})",
            "p95 ≤ 2500 ms (pułap OPERACYJNY shadow, PROWIZORYCZNY; ideał 500ms=pre-Hetzner)",
            "prowizoryczna",
            note="latencja SHADOW; ogon peak = flota/kontencja (sprint A perf w toku) — NIE regresja silnika",
        )
    elif sh.get("data_ok"):
        add("latency_p95", "Latencja decyzji p95 (shadow)", "shadow_decisions", GREY,
            None, f"za mało danych (n={sh.get('lat_n',0)})", "p95 ≤2500ms", "prowizoryczna", "")

    # M7 — proposal churn / flicker (≤ ceiling, PROWIZORYCZNY).
    if ch.get("data_ok"):
        last = ch["last"]
        stale = _churn_stale(last["day"], now, days=3)
        burn, color = slo_burn(last["ge3_pct"], 45.0, "le")
        add(
            "proposal_flicker",
            "Migotanie propozycji (flicker ≥3 zmian)",
            "proposal_churn",
            color if not stale else GREY,
            None if stale else burn,
            (f"za mało danych (ostatnia doba {last['day']} > 3 dni temu)" if stale
             else f"{last['day']}: ≥3 zmian {_fmt(last['ge3_pct'],1,'%')} (≥1 {_fmt(last['ge1_pct'],1,'%')}, flick_same {_fmt(last['flick_same_pct'],1,'%')}, N={last['n']})"),
            "flicker ≥3 zmian ≤ 45% (próg PROWIZORYCZNY — ACK Adriana)",
            "prowizoryczna",
            note="raport churnu domyka się dobę wstecz — nie dzisiejszy" if not stale else "brak świeżej doby",
        )
    else:
        add("proposal_flicker", "Migotanie propozycji", "proposal_churn", GREY, None,
            "za mało danych", "flicker ≥3 ≤45%", "prowizoryczna", "brak wierszy per-doba")

    # M9 — pickup slip (INFORMACYJNE — wejście do kalibracji, nie SLO-alarm).
    if ps.get("data_ok"):
        stale = _is_stale(ps.get("ts"), now, hours=72)
        add(
            "pickup_slip",
            "Poślizg odbioru (optymizm ETA)",
            "pickup_slip_monitor",
            YELLOW if stale else GREEN,
            None,
            f"mediana solo {_fmt(ps.get('solo_median'),1)} min (n={ps.get('solo_n')}), bundle {_fmt(ps.get('bundle_median'),1)} min (n={ps.get('bundle_n')}); okno {ps.get('window_days')}d",
            "informacyjne: DODATNI=optymistyczny; bufor tylko gdy n≥30 (nie SLO-alarm)",
            "informacyjna",
            note="⚠ rekord nieświeży (>72h)" if stale else "wejście do ewentualnego bufora ETA (protokół+ACK przed flipem)",
        )

    # M-informacyjne: redirecty + rozkład werdyktów (kontekst, bez SLO).
    if sh.get("data_ok"):
        rd = sh["redirects"]
        rd_txt = ", ".join(f"{k.replace('_redirect','')}={v}" for k, v in rd.items() if v) or "brak"
        add(
            "shadow_context",
            "Kontekst decyzji (werdykty / redirecty)",
            "shadow_decisions",
            GREEN if sh["n"] else GREY,
            None,
            f"n={sh['n']}; werdykty={sh['verdict']}; auto_route={sh['auto_route']}; redirecty: {rd_txt}",
            "informacyjne (kontekst, bez progu SLO)",
            "informacyjna",
        )

    return {"metrics": metrics, "attention": attention}


def _is_stale(ts_iso, now, hours):
    ts = parse_ts(ts_iso)
    if ts is None:
        return True
    return (now - ts) > timedelta(hours=hours)


def _churn_stale(day_str, now, days):
    try:
        d = datetime.strptime(day_str, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    except ValueError:
        return True
    return (now.date() - d.date()).days > days


# ─────────────────────────────────────────────────────────────────────────────
# RENDER (E4 karta markdown + JSON).
# ─────────────────────────────────────────────────────────────────────────────
def _overall(metrics):
    """Najgorszy status wśród metryk SLO (informacyjne pomijamy)."""
    slo = [m for m in metrics if m["confidence"] != "informacyjna"]
    if any(m["status"] == RED for m in slo):
        return RED
    if any(m["status"] == YELLOW for m in slo):
        return YELLOW
    if any(m["status"] == GREEN for m in slo):
        return GREEN
    return GREY


def render_markdown(card, window_hours, now, sources):
    metrics = card["metrics"]
    L = []
    overall = _overall(metrics)
    L.append(f"# 🩺 Tablica zdrowia Ziomka + budżet błędu (SLO) — {overall}")
    L.append("")
    L.append(f"**Wygenerowano:** {now.isoformat()} · **okno decyzji:** {window_hours}h · "
             f"agregator READ-ONLY (Sprint E) · źródeł: 6")
    L.append("")
    L.append("Kolory budżetu: 🟢 burn <75% · 🟡 75–100% · 🔴 >100% (budżet przekroczony) · "
             "⚪ za mało danych. „Twarda”=cel z danych/inwariantu; „prowizoryczna”=próg heurystyczny (ACK Adriana).")
    L.append("")

    # Tabela główna.
    L.append("| Stan | Metryka | Wartość | Budżet (burn) | SLO | Źródło |")
    L.append("|:--:|---|---|:--:|---|---|")
    for m in metrics:
        if m["confidence"] == "informacyjna":
            continue
        burn = f"{m['burn_pct']:.0f}%" if isinstance(m["burn_pct"], (int, float)) else "—"
        conf = "🔒" if m["confidence"] == "twarda" else "≈"
        L.append(f"| {m['status']} | {m['label']} | {m['value']} | {burn} | {conf} {m['slo']} | `{m['source']}` |")
    L.append("")

    # Uwaga Adriana.
    L.append("## ⚠️ Co wymaga uwagi Adriana")
    att = card["attention"]
    # dorzuć 🟡 prowizoryczne z pełnym budżetem jako „obserwacja"
    watch = [m for m in metrics if m["status"] == YELLOW and m["confidence"] != "informacyjna"]
    if not att and not watch:
        L.append("- 🟢 Brak czerwonych progów. Wszystkie twarde SLO w budżecie.")
    else:
        for key, label, why in att:
            L.append(f"- 🔴 **{label}** — {why}")
        for m in watch:
            burn = f"{m['burn_pct']:.0f}%" if isinstance(m["burn_pct"], (int, float)) else "—"
            L.append(f"- 🟡 **{m['label']}** — budżet zjedzony {burn}. {m['note'] or m['slo']}")
    L.append("")

    # Informacyjne / kontekst.
    L.append("## ℹ️ Kontekst (informacyjne — bez progu SLO)")
    for m in metrics:
        if m["confidence"] != "informacyjna":
            continue
        L.append(f"- **{m['label']}** — {m['value']}"
                 + (f" · _{m['note']}_" if m["note"] else ""))
    L.append("")

    # E3 — trendy + znaczniki flipów (OBSERWACYJNE, NIE test istotności).
    L.append("## 📈 Trendy + żywe flipy (obserwacja — NIE dowód istotności)")
    L.append("")
    L.append("> Wpływ ON/OFF żywych flag pokazany jako TREND per-doba/rekord ze znacznikami "
             "znanych flipów. To obserwacja, **nie** test istotności — okna 2-dniowe nie są "
             "domknięte, więc przyczynowości NIE orzekamy. Gdzie serii brak → „za mało danych”.")
    L.append("")
    L.append("**Znane żywe flipy/cienie (kontekst dat):** K2 geometria + K3 claim-ledger LIVE ~05.07 · "
             "O2-K1 flip (werdykt ~09.07) · CHECK claim-ledger invariant (log-loud LIVE, ACK 08.07) · "
             "kalibracja ETA — w CIENIU (flaga OFF).")
    L.append("")
    _render_trends(L, sources)
    L.append("")

    # Stopka.
    L.append("---")
    L.append("_Agregator `tools/health_scoreboard.py` — czysto czyta cudze logi, "
             "zapisuje wyłącznie tę kartę. Zero mutacji kolektorów/silnika. "
             "Progi „prowizoryczne” wymagają potwierdzenia Adriana._")
    return "\n".join(L)


def _render_trends(L, sources):
    eta = sources["eta_calib"]
    ng = sources["night_guard"]
    ch = sources["churn"]
    ps = sources["pickup_slip"]

    if eta.get("data_ok") and eta.get("series"):
        L.append("**ETA on-time (cień) — per rekord kalibracji:**")
        L.append("")
        L.append("| logged_at | odbiór on-time | dostawa on-time |")
        L.append("|---|:--:|:--:|")
        for r in eta["series"][-6:]:
            L.append(f"| {r['logged_at']} | {_fmt(r['pickup_ontime'],1,'%')} | {_fmt(r['delivery_ontime'],1,'%')} |")
        L.append("")

    if ng.get("data_ok") and ng.get("series"):
        L.append("**Nocny guard — per noc (failed / entropia poison):**")
        L.append("")
        L.append("| ts | failed | verdict | poison_live | poison_instr |")
        L.append("|---|:--:|:--:|:--:|:--:|")
        for r in ng["series"][-6:]:
            L.append(f"| {r['ts']} | {r['failed']} | {r['verdict']} | {r['poison_live']} | {r['poison_instr']} |")
        L.append("")

    if ch.get("data_ok") and ch.get("series"):
        L.append("**Migotanie propozycji — per doba (≥3 zmian %):**")
        L.append("")
        L.append("| doba | N | ≥1% | ≥3% | flick_same% |")
        L.append("|---|:--:|:--:|:--:|:--:|")
        for r in ch["series"][-7:]:
            L.append(f"| {r['day']} | {r['n']} | {_fmt(r['ge1_pct'],1,'%')} | {_fmt(r['ge3_pct'],1,'%')} | {_fmt(r['flick_same_pct'],1,'%')} |")
        L.append("")

    if ps.get("data_ok") and ps.get("series") and len(ps["series"]) > 1:
        L.append("**Poślizg odbioru — n_total per przebieg (kontekst wolumenu):**")
        L.append("")
        vals = ", ".join(f"{r['ts'][:10]}={r['n_total']}" for r in ps["series"][-6:])
        L.append(f"{vals}")
        L.append("")


def atomic_write(path, text):
    """temp → fsync → rename (atomowy zapis, wzorzec repo)."""
    d = os.path.dirname(path) or "."
    fd, tmp = tempfile.mkstemp(dir=d, prefix=".hsb_", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(text)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def collect_sources(window_hours, now, paths=None, max_scan_bytes=DEFAULT_MAX_SCAN_BYTES):
    paths = paths or {}
    since = now - timedelta(hours=window_hours)
    return {
        "shadow": load_shadow(paths.get("shadow", SRC_SHADOW), since, max_scan_bytes),
        "eta_calib": load_eta_calib(paths.get("eta_calib", SRC_ETA_CALIB)),
        "resweep": load_resweep(paths.get("resweep", SRC_RESWEEP), since, max_scan_bytes),
        "churn": load_churn(paths.get("churn", SRC_CHURN)),
        "night_guard": load_night_guard(paths.get("night_guard", SRC_NIGHT_GUARD)),
        "pickup_slip": load_pickup_slip(paths.get("pickup_slip", SRC_PICKUP_SLIP)),
    }


def main(argv=None):
    ap = argparse.ArgumentParser(description="Tablica zdrowia + budżet błędu (READ-ONLY).")
    ap.add_argument("--window-hours", type=int, default=24,
                    help="okno wstecz dla shadow/resweep (default 24)")
    ap.add_argument("--out-dir", default=OUT_DIR_DEFAULT,
                    help=f"katalog karty (default {OUT_DIR_DEFAULT})")
    ap.add_argument("--asof", help="ISO 'teraz' (determinizm testów); default = now UTC")
    ap.add_argument("--stdout", action="store_true", help="wypisz kartę na stdout, NIE zapisuj plików")
    ap.add_argument("--max-scan-mb", type=int, default=DEFAULT_MAX_SCAN_BYTES // (1024 * 1024),
                    help="bajtowy ogon na plik przy skanie dużych jsonl")
    args = ap.parse_args(argv)

    now = parse_ts(args.asof) if args.asof else datetime.now(timezone.utc)
    if now is None:
        print("[health_scoreboard] zły --asof", file=sys.stderr)
        return 2
    max_bytes = max(1, args.max_scan_mb) * 1024 * 1024

    sources = collect_sources(args.window_hours, now, max_scan_bytes=max_bytes)
    card = build_card(args.window_hours, now, sources)
    md = render_markdown(card, args.window_hours, now, sources)
    payload = {
        "generated_at": now.isoformat(),
        "window_hours": args.window_hours,
        "overall": _overall(card["metrics"]),
        "metrics": card["metrics"],
        "attention": [{"key": k, "label": l, "why": w} for k, l, w in card["attention"]],
        "sources_summary": {
            "shadow_n": sources["shadow"].get("n"),
            "resweep_n": sources["resweep"].get("n"),
            "eta_calib_ok": sources["eta_calib"].get("data_ok"),
            "churn_ok": sources["churn"].get("data_ok"),
            "night_guard_ok": sources["night_guard"].get("data_ok"),
            "pickup_slip_ok": sources["pickup_slip"].get("data_ok"),
        },
    }
    if args.stdout:
        print(md)
        return 0
    os.makedirs(args.out_dir, exist_ok=True)
    atomic_write(os.path.join(args.out_dir, CARD_MD), md)
    atomic_write(os.path.join(args.out_dir, CARD_JSON),
                 json.dumps(payload, ensure_ascii=False, indent=2))
    print(f"[health_scoreboard] karta: {os.path.join(args.out_dir, CARD_MD)} · stan={payload['overall']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())


# ─────────────────────────────────────────────────────────────────────────────
# PROPOZYCJA TIMERA (DO PRZYSZŁEGO WDROŻENIA ZA ACK — NIE INSTALOWAĆ TERAZ)
# Wzór: dispatch-proposal-churn.{service,timer}. READ-ONLY konsument; jedyny zapis
# = własna karta w dispatch_state/. Instalacja = decyzja Adriana (ETAP 6).
#
# /etc/systemd/system/dispatch-health-scoreboard.service
#   [Unit]
#   Description=Tablica zdrowia + budzet bledu (read-only agregator SLO)
#   [Service]
#   Type=oneshot
#   WorkingDirectory=/root/.openclaw/workspace/scripts
#   ExecStart=/root/.openclaw/venvs/dispatch/bin/python \
#       /root/.openclaw/workspace/scripts/dispatch_v2/tools/health_scoreboard.py --window-hours 24
#   # czyta 6 logow; pisze WYLACZNIE dispatch_state/health_scoreboard_card.{md,json}
#
# /etc/systemd/system/dispatch-health-scoreboard.timer
#   [Unit]
#   Description=Dzienna karta zdrowia Ziomka
#   [Timer]
#   OnCalendar=*-*-* 05:30:00 UTC        # po churn(05:15) i eta_calib(05:20) — swieze wejscia
#   Persistent=true
#   [Install]
#   WantedBy=timers.target
#
# Po ACK: systemctl daemon-reload && systemctl enable --now dispatch-health-scoreboard.timer
# ─────────────────────────────────────────────────────────────────────────────
