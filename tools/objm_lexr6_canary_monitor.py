#!/usr/bin/env python3
"""CANARY MONITOR objm-lexr6 Faza 2 (READ-ONLY).

Liczy metryki drugiego rzędu live-flipu `ENABLE_OBJM_LEXR6_SELECT` w oknie czasowym i
porównuje do baseline → zwraca per-gate GO/STOP/WARN (plan: CANARY_PLAN_objm_lexr6.md).

Źródła (tylko ODCZYT):
- shadow_decisions.jsonl → verdict (KOORD%), auto_route (AUTO/ACK/ALERT%), latency_ms (p50/p95), n.
- dispatch.log / watcher.log → reorder count (`OBJM_LEXR6_SELECT order=… reorder→cid=`) + błędy
  (`OBJM_LEXR6_SELECT pick failed`).
- flags.json → czy SELECT faktycznie ON (canary aktywne) + czy SHADOW omyłkowo nadal ON.

Tryby:
  --save-baseline       policz okno i ZAPISZ jako baseline (Faza 0, przed flipem)
  (domyślny)            policz okno, porównaj do baseline, wypisz gate'y
  --window-min N        długość okna wstecz (default 120)
  --notify              wyślij Telegram przy STOP (fail-soft; guard PYTEST_CURRENT_TEST)
  --baseline PATH       ścieżka baseline (default dispatch_state/objm_lexr6_canary_baseline.json)

NIE mutuje stanu produkcyjnego. Fail-soft. Wzór: carried_first_peak_monitor.

L1.2 (2026-07-01): odczyt shadow_decisions przepięty na kanon
`ledger_io.iter_shadow_decisions` (jedno źródło odczytu ledgera zamiast lokalnej
kopii `_rot_lines`; semantyka metryk/bramek BEZ ZMIAN — per-rekord filtr ts
zostaje tu). `_rot_lines` zostaje TYLKO dla dispatch.log/watcher.log (nie-ledger).
"""
import json, os, sys, glob, gzip, argparse, re
from datetime import datetime, timezone, timedelta

SCRIPTS = "/root/.openclaw/workspace/scripts"
sys.path.insert(0, SCRIPTS)
from dispatch_v2.tools import ledger_io  # noqa: E402

def _shadow_path():
    """Ścieżka kanonu shadow — czytana dynamicznie (testy patchują ledger_io.LEDGER)."""
    return ledger_io.LEDGER["shadow"]


LOGS = [f"{SCRIPTS}/logs/dispatch.log", f"{SCRIPTS}/logs/watcher.log"]
FLAGS = f"{SCRIPTS}/flags.json"
BASELINE_DEFAULT = "/root/.openclaw/workspace/dispatch_state/objm_lexr6_canary_baseline.json"
# EDGE-TRIGGERED notify: stan ostatnio wysłanego werdyktu (sygnatura+poziom+czas) — alert tylko
# gdy werdykt SIĘ ZMIENI (nowy gate / eskalacja / powrót do GO), nie co tick. Patrz _notify_decision.
NOTIFY_STATE = "/root/.openclaw/workspace/dispatch_state/objm_lexr6_canary_notify_state.json"
# Utrzymujący się STOP/WARN przypominaj nie częściej niż co tyle h (env-overridable). 0 = bez przypomnień.
NOTIFY_REMIND_H = float(os.environ.get("CANARY_NOTIFY_REMIND_H", "2.0"))

# Progi gate'ów (env-overridable; KANON = plan, Adrian potwierdza domenę)
KOORD_STOP_PP   = float(os.environ.get("CANARY_KOORD_STOP_PP", "5.0"))
ACKALERT_STOP_PP= float(os.environ.get("CANARY_ACKALERT_STOP_PP", "8.0"))
LAT_P95_STOP_PCT= float(os.environ.get("CANARY_LAT_P95_STOP_PCT", "15.0"))
REORDER_LO      = float(os.environ.get("CANARY_REORDER_LO_PCT", "5.0"))
REORDER_HI      = float(os.environ.get("CANARY_REORDER_HI_PCT", "25.0"))
REORDER_MATCH_S = float(os.environ.get("CANARY_REORDER_MATCH_S", "5.0"))  # ±s match reorder→DECYZJA (#6a audyt)
# MIN-SAMPLE: poniżej tylu decyzji w oknie gate'y STATYSTYCZNE degradujemy STOP/WARN→INFO
# (off-peak n jest strukturalnie maleńkie → szum). G1-błędy (pick failed) NIGDY nie wyciszane.
MIN_N_FOR_STOP    = int(os.environ.get("CANARY_MIN_N_FOR_STOP", "30"))
TOD_BASELINE_DAYS = int(os.environ.get("CANARY_TOD_DAYS", "7"))
# G2a-KOORD ma własną próbę selektor-istotną (n_sel = excl early_bird) → guard inline, NIE tu.
_SUPPRESSIBLE_UNDER_MIN_N = {"G2b-auto-route", "G2c-reorder", "G1-latencja"}
# order_id z linii reorderu — dedup G2c po orderze (jedna linia/order, NIE per re-ewaluacja sweepera)
_REORDER_OID_RE = re.compile(r"OBJM_LEXR6_SELECT order=(\d+) reorder")


def _pct(part, whole):
    return (100.0 * part / whole) if whole else 0.0


def _pctile(vals, q):
    if not vals:
        return None
    s = sorted(vals)
    i = min(len(s) - 1, int(q * (len(s) - 1) + 0.5))
    return s[i]


def _parse_iso(ts):
    if not ts:
        return None
    try:
        return datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
    except Exception:
        return None


def _parse_log_ts(line):
    # format "2026-06-24 05:44:05 [INFO] ..." → naiwny UTC (logi serwera = UTC)
    try:
        d = datetime.strptime(line[:19], "%Y-%m-%d %H:%M:%S")
        return d.replace(tzinfo=timezone.utc)
    except Exception:
        return None


def _rot_lines(base, since):
    """Yield linie z `base` + rotacji `base.1[.gz]` (logrotate daily/copytruncate). Pomija
    pliki, których mtime < since (cała rotacja starsza niż okno → nie czytaj 100MB). Odporne
    na rotację W TRAKCIE okna (ta sama klasa pułapki co fałszywy HOLD walidatora 2026-06-25)."""
    since_ts = since.timestamp()
    for p in sorted(glob.glob(base + "*")):
        try:
            if os.path.getmtime(p) < since_ts:
                continue
        except OSError:
            continue
        opener = gzip.open if p.endswith(".gz") else open
        try:
            with opener(p, "rt", encoding="utf-8", errors="replace") as f:
                for line in f:
                    yield line
        except Exception as e:
            print(f"[skip rot {p}: {e!r}]")


def _is_early_bird(r):
    """KOORD z powodu `early_bird` (zlecenie złożone na długo przed odbiorem → koordynator
    przytrzymuje). Wynik NIEZALEŻNY od wyboru kuriera → selektor objm-lexr6 nie ma na to wpływu,
    więc wykluczamy z metryki G2a (num I denom), spójnie w oknie i w baseline."""
    return str(r.get("reason") or "").strip().startswith("early_bird")


def shadow_metrics(since):
    n = koord = koord_eb = 0
    auto = {"AUTO": 0, "ACK": 0, "ALERT": 0}
    lats = []
    order_ids = set()  # distinct order_id decydowanych w oknie (legacy)
    decision_events = []  # #6a audyt: (oid|None, ts) per DECYZJA → mianownik per-decyzja G2c
    if not os.path.exists(_shadow_path()):
        return None
    for r in ledger_io.iter_shadow_decisions(since):
        t = _parse_iso(r.get("ts"))
        if t is None or t < since:
            continue
        n += 1
        oid = r.get("order_id")
        decision_events.append((str(oid) if oid is not None else None, t))
        if oid is not None:
            order_ids.add(str(oid))
        if str(r.get("verdict")) == "KOORD":
            koord += 1
            if _is_early_bird(r):
                koord_eb += 1
        a = str(r.get("auto_route") or "")
        if a in auto:
            auto[a] += 1
        lm = r.get("latency_ms")
        if isinstance(lm, (int, float)):
            lats.append(float(lm))
    # metryka selektor-istotna: wyklucz early_bird (od selektora niezależne) z num I denom
    koord_sel = koord - koord_eb
    n_sel = n - koord_eb
    return {
        "n": n,
        "n_orders": len(order_ids),
        "shadow_oids": order_ids,
        "decision_events": decision_events,
        "koord_pct": round(_pct(koord, n), 2),            # raw (transparencja / flat fallback)
        "koord_eb": koord_eb,
        "n_sel": n_sel,
        "koord_sel": koord_sel,
        "koord_pct_sel": round(_pct(koord_sel, n_sel), 2),  # G2a używa TEGO (excl early_bird)
        "ack_alert_pct": round(_pct(auto["ACK"] + auto["ALERT"], n), 2),
        "auto_pct": round(_pct(auto["AUTO"], n), 2),
        "lat_p50": _pctile(lats, 0.50),
        "lat_p95": _pctile(lats, 0.95),
    }


def log_signals(since):
    reorders = errors = 0
    reorder_oids = set()  # distinct order_id z reorderem (legacy, all-tick — diagnostyka raw)
    reorder_events = {}   # #6a audyt: oid → [ts linii reorder]. Per-DECYZJA G2c matchuje do ts
                          # proposala (±REORDER_MATCH_S), NIE „reorder w JAKIMKOLWIEK ticku".
    for base in LOGS:
        for line in _rot_lines(base, since):
            if "OBJM_LEXR6_SELECT" not in line:
                continue
            t = _parse_log_ts(line)
            if t is None or t < since:
                continue
            if "pick failed" in line:
                errors += 1
            elif "reorder" in line:
                reorders += 1
                m = _REORDER_OID_RE.search(line)
                if m:
                    reorder_oids.add(m.group(1))
                    reorder_events.setdefault(m.group(1), []).append(t)
    return {"reorders": reorders, "errors": errors,
            "reorder_oids": reorder_oids, "reorder_events": reorder_events}


def flag_state():
    try:
        d = json.load(open(FLAGS))
        return {
            "select_on": bool(d.get("ENABLE_OBJM_LEXR6_SELECT", False)),
            "shadow_on": bool(d.get("ENABLE_OBJM_LEXR6_SELECT_SHADOW", False)),
        }
    except Exception:
        return {"select_on": None, "shadow_on": None}


def compute_tod_curve(days, cutoff):
    """Per-godzina UTC {koord%, n} z shadow_decisions PRZED `cutoff` (=SELECT-OFF), ostatnie `days` dni.
    Zwraca (koord_by_hour, n_by_hour) z kluczami-stringami (json-friendly). READ-ONLY."""
    from collections import defaultdict
    agg = defaultdict(lambda: [0, 0, 0])  # hour -> [n, koord, koord_early_bird]
    start = cutoff - timedelta(days=days)
    if not os.path.exists(_shadow_path()):
        return {}, {}
    for r in ledger_io.iter_shadow_decisions(start):
        t = _parse_iso(r.get("ts"))
        if t is None or t < start or t >= cutoff:
            continue
        a = agg[t.hour]
        a[0] += 1
        if str(r.get("verdict")) == "KOORD":
            a[1] += 1
            if _is_early_bird(r):
                a[2] += 1
    # krzywa SELEKTOR-ISTOTNA: per godzina wyklucz early_bird z num I denom (parytet z shadow_metrics)
    koord_by_hour, n_by_hour = {}, {}
    for h, (n, k, eb) in agg.items():
        n_sel = n - eb
        if n_sel <= 0:
            continue
        n_by_hour[str(h)] = n_sel
        koord_by_hour[str(h)] = round(_pct(k - eb, n_sel), 2)
    return koord_by_hour, n_by_hour


def _overlap_min(a0, a1, b0, b1):
    """Minuty pokrycia [a0,a1] ∩ [b0,b1]."""
    lo = max(a0, b0)
    hi = min(a1, b1)
    return max(0.0, (hi - lo).total_seconds() / 60.0)


def _expected_koord_tod(base, since, now):
    """Oczekiwany KOORD% dla SPAN okna [since, now] złożony z krzywej per-godzina (SELECT-OFF)
    ważonej wolumenem historycznym × minutami pokrycia każdej godziny. Precyzyjnie obsługuje okno
    straddlujące klif KOORD (np. 07→08). Zwraca float albo None (brak krzywej / zero wagi → fallback)."""
    if not base:
        return None
    kbh = base.get("koord_by_hour")
    nbh = base.get("n_by_hour")
    if not kbh or not nbh:
        return None
    num = den = 0.0
    one = timedelta(hours=1)
    h = since.replace(minute=0, second=0, microsecond=0)
    while h <= now:
        key = str(h.hour)
        n_h = nbh.get(key)
        k_h = kbh.get(key)
        if n_h and k_h is not None:
            w = float(n_h) * _overlap_min(since, now, h, h + one) / 60.0
            num += w * float(k_h)
            den += w
        h += one
    if den <= 0:
        return None
    return num / den


def gates(cur, log, flags, base, since, now):
    """Zwróć listę (gate, status, detal). status ∈ GO/STOP/WARN/INFO."""
    out = []
    n = cur["n"]
    # G2c PER-DECYZJA (#6a audyt 28.06): pasmo 5-25% (~12%) skalibrowane PER-DECYZJA
    # (12,1%=174/1435). Poprzednio licznik=order reorderowany w JAKIMKOLWIEK ticku ∩ decydowane,
    # mianownik=distinct order → MIESZAŁO populacje (all-tick vs per-decyzja): 62,5% raport vs
    # 17,9% prawda (×3,5) → fałszywy WARN „over-reorder". TERAZ: licznik = DECYZJE, których tick
    # miał linię reorder TEGO orderu w ±REORDER_MATCH_S; mianownik = wszystkie decyzje.
    decision_events = cur.get("decision_events") or []
    reorder_events = log.get("reorder_events") or {}
    _win = REORDER_MATCH_S
    reorder_dec = sum(
        1 for (oid, td) in decision_events
        if oid and any(abs((tr - td).total_seconds()) <= _win for tr in reorder_events.get(oid, []))
    )
    n_dec = len(decision_events)
    reorder_pct = _pct(reorder_dec, n_dec) if n_dec else 0.0
    # legacy all-tick (diagnostyka raw — pokazywane obok, NIE bramkuje):
    shadow_oids = cur.get("shadow_oids") or set()
    reorder_oids = log.get("reorder_oids") or set()
    n_orders = cur.get("n_orders", n)
    reorder_orders_alltick = len(reorder_oids & shadow_oids) if shadow_oids else len(reorder_oids)

    # G1 zdrowie
    if log["errors"] > 0:
        out.append(("G1-błędy", "STOP", f"{log['errors']}× 'pick failed'"))
    else:
        out.append(("G1-błędy", "GO", "0 pick-failed"))

    if base and base.get("lat_p95") and cur["lat_p95"] is not None:
        lim = base["lat_p95"] * (1 + LAT_P95_STOP_PCT / 100.0)
        st = "STOP" if cur["lat_p95"] > lim else "GO"
        out.append(("G1-latencja", st, f"p95 {cur['lat_p95']:.0f} vs baseline {base['lat_p95']:.0f}ms (limit +{LAT_P95_STOP_PCT:.0f}%={lim:.0f})"))
    else:
        out.append(("G1-latencja", "INFO", f"p95 {cur['lat_p95']}ms (brak baseline)"))

    # G2a KOORD — (1) SELEKTOR-ISTOTNA: wyklucz early_bird (od selektora niezależne) z num I denom;
    # (2) TIME-OF-DAY aware: porównaj do oczek. KOORD% dla pory dnia okna (krzywa per-godzina,
    # też excl early_bird). Próg +5pp (KANON planu) zachowany. Min-n liczony na n_sel (próba istotna).
    koord_sel_pct = cur.get("koord_pct_sel", cur["koord_pct"])
    n_sel = cur.get("n_sel", cur["n"])
    eb = cur.get("koord_eb", 0)
    _raw = f"; raw {cur['koord_pct']:.1f}% (early_bird {eb})"
    exp_tod = _expected_koord_tod(base, since, now)
    if n_sel < MIN_N_FOR_STOP:
        out.append(("G2a-KOORD", "INFO", f"[n_sel={n_sel}<{MIN_N_FOR_STOP} za mała próba selektor-istotna — wyciszony] sel {koord_sel_pct:.1f}%{_raw}"))
    elif exp_tod is not None:
        d = koord_sel_pct - exp_tod
        st = "STOP" if d > KOORD_STOP_PP else "GO"
        out.append(("G2a-KOORD", st, f"sel {koord_sel_pct:.1f}% (excl early_bird) vs oczek.(tod) {exp_tod:.1f}% (Δ{d:+.1f}pp, limit +{KOORD_STOP_PP:.0f}){_raw}"))
    elif base and base.get("koord_pct") is not None:
        d = koord_sel_pct - base["koord_pct"]
        st = "STOP" if d > KOORD_STOP_PP else "GO"
        out.append(("G2a-KOORD", st, f"sel {koord_sel_pct:.1f}% (excl early_bird) vs baseline {base['koord_pct']:.1f}% (Δ{d:+.1f}pp, limit +{KOORD_STOP_PP:.0f}) [flat — brak krzywej tod]{_raw}"))
    else:
        out.append(("G2a-KOORD", "INFO", f"sel {koord_sel_pct:.1f}% (brak baseline){_raw}"))

    # G2b auto-route
    if base and base.get("ack_alert_pct") is not None:
        d = cur["ack_alert_pct"] - base["ack_alert_pct"]
        st = "STOP" if d > ACKALERT_STOP_PP else "GO"
        out.append(("G2b-auto-route", st, f"ACK+ALERT {cur['ack_alert_pct']:.1f}% vs {base['ack_alert_pct']:.1f}% (Δ{d:+.1f}pp, limit +{ACKALERT_STOP_PP:.0f}); AUTO {cur['auto_pct']:.1f}%"))
    else:
        out.append(("G2b-auto-route", "INFO", f"ACK+ALERT {cur['ack_alert_pct']:.1f}% / AUTO {cur['auto_pct']:.1f}% (brak baseline)"))

    # G2c reorder sanity (PER-DECYZJA ±match; tylko gdy SELECT faktycznie ON)
    if flags.get("select_on"):
        _at_pct = _pct(reorder_orders_alltick, n_orders) if n_orders else 0.0
        _det = (f"per-decyzja {reorder_pct:.1f}% ({reorder_dec}/{n_dec} decyzji z reorderem ±{_win:.0f}s; "
                f"oczek. ~12%, pas {REORDER_LO:.0f}-{REORDER_HI:.0f}%) | all-tick {reorder_orders_alltick}/{n_orders} "
                f"ord = {_at_pct:.1f}% (diagnostyka, ZAWYŻONE ×~3,5) | raw {log['reorders']} linii")
        st = "WARN" if (reorder_pct < REORDER_LO or reorder_pct > REORDER_HI) else "GO"
        out.append(("G2c-reorder", st, _det))
    else:
        out.append(("G2c-reorder", "INFO", "SELECT OFF — canary nieaktywne"))

    # hygiena: SHADOW nie powinien być ON razem z SELECT
    if flags.get("select_on") and flags.get("shadow_on"):
        out.append(("hygiena-shadow", "WARN", "SELECT i SHADOW oba ON → shadow liczy się po mutacji (zaślepia + double-compute); ustaw SHADOW=false"))

    # MIN-SAMPLE guard: przy małej próbie (typowo off-peak poranek) gate'y STATYSTYCZNE są szumem
    # → degraduj ich STOP/WARN do INFO (nie spamuj Telegrama). G1-błędy (realny pick-failed) zostaje.
    if n < MIN_N_FOR_STOP:
        degraded = []
        for name, st, det in out:
            if name in _SUPPRESSIBLE_UNDER_MIN_N and st in ("STOP", "WARN"):
                degraded.append((name, "INFO", f"[n={n}<{MIN_N_FOR_STOP} za mała próba — wyciszony] {det}"))
            else:
                degraded.append((name, st, det))
        out = degraded
    return out


def _load_notify_state():
    try:
        with open(NOTIFY_STATE) as f:
            return json.load(f)
    except Exception:
        return {}


def _save_notify_state(d):
    import tempfile
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(NOTIFY_STATE), prefix=".notify_state_")
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(d, f, ensure_ascii=False)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, NOTIFY_STATE)
    except Exception:
        try:
            os.unlink(tmp)
        except Exception:
            pass
        raise


def _verdict_signature(stops, warns):
    """(poziom, sygnatura) werdyktu. Sygnatura zmienia się gdy zmieni się poziom LUB zbiór
    odpalonych gate'ów (nazwa:stan) — to wyzwala edge-triggered alert."""
    level = "STOP" if stops else ("WARN" if warns else "GO")
    items = ";".join(f"{n}:{s}" for n, s, _ in sorted(list(stops) + list(warns), key=lambda x: x[0]))
    return level, f"{level}|{items}"


def _notify_decision(stops, warns, prev, now, remind_after):
    """Pure (testowalne): czy wysłać Telegram (edge-triggered) + treść + nowy stan.

    Reguła „w odpowiednim momencie, nie co tick":
      - GO: alert TYLKO gdy poprzednio było STOP/WARN (powrót do normy), inaczej cisza.
      - STOP/WARN: alert gdy sygnatura ≠ poprzednia (nowy/eskalacja), albo gdy ten sam
        stan utrzymuje się dłużej niż remind_after (rzadkie przypomnienie). Inaczej cisza.
    Zwraca (send: bool, msg: str|None, new_state: dict).
    """
    level, sig = _verdict_signature(stops, warns)
    prev_sig = prev.get("signature")
    prev_level = prev.get("level")
    prev_sent_raw = prev.get("last_sent")
    prev_sent = _parse_iso(prev_sent_raw) if prev_sent_raw else None
    send, msg = False, None
    if level == "GO":
        if prev_level and prev_level != "GO":
            send = True
            msg = f"🟢 CANARY objm-lexr6 GO — werdykt wrócił do normy (był {prev_level})"
    else:
        head = "🔴 CANARY objm-lexr6 STOP" if stops else "🟡 CANARY objm-lexr6 WARN"
        detail = "; ".join(f"{n}:{s}" for n, s, _ in (stops or warns))
        if sig != prev_sig:
            send, msg = True, f"{head} | {detail}"
        elif remind_after and (prev_sent is None or (now - prev_sent) >= remind_after):
            hh = remind_after.total_seconds() / 3600.0
            send, msg = True, f"{head} (nadal >{hh:.0f}h) | {detail}"
    new_sent = now.isoformat() if send else prev_sent_raw
    return send, msg, {"signature": sig, "level": level, "last_sent": new_sent}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--window-min", type=int, default=120)
    ap.add_argument("--save-baseline", action="store_true")
    ap.add_argument("--save-tod-baseline", action="store_true",
                    help="policz krzywą KOORD%/godzina (SELECT-OFF, pre-cutoff) i WMERGUJ do baseline (nie kasuje innych pól)")
    ap.add_argument("--tod-cutoff", default=None, help="ISO UTC — dane PRZED tym = SELECT-OFF (default: now)")
    ap.add_argument("--tod-days", type=int, default=TOD_BASELINE_DAYS)
    ap.add_argument("--baseline", default=BASELINE_DEFAULT)
    ap.add_argument("--notify", action="store_true")
    a = ap.parse_args()

    now = datetime.now(timezone.utc)
    since = now - timedelta(minutes=a.window_min)
    cur = shadow_metrics(since)
    if cur is None:
        print("BRAK shadow_decisions.jsonl — nie mogę liczyć."); return 1
    log = log_signals(since)
    flags = flag_state()

    if a.save_baseline:
        snap = {k: v for k, v in cur.items() if not isinstance(v, set)}  # set (shadow_oids) niejson-owalny
        snap["saved_at"] = now.isoformat()
        snap["window_min"] = a.window_min
        try:
            tmp = a.baseline + ".tmp"
            with open(tmp, "w") as f:
                json.dump(snap, f, indent=2, ensure_ascii=False)
            os.replace(tmp, a.baseline)
            print(f"[baseline zapisany {a.baseline}]")
            print(json.dumps(snap, indent=2, ensure_ascii=False))
        except Exception as e:
            print(f"[zapis baseline fail: {e!r}]"); return 1
        return 0

    if a.save_tod_baseline:
        cutoff = _parse_iso(a.tod_cutoff) if a.tod_cutoff else now
        if cutoff is None:
            print(f"[--tod-cutoff nieparsowalne: {a.tod_cutoff!r}]"); return 1
        kbh, nbh = compute_tod_curve(a.tod_days, cutoff)
        if not kbh:
            print("[brak danych SELECT-OFF do krzywej TOD — nic nie zapisano]"); return 1
        existing = {}
        if os.path.exists(a.baseline):
            try:
                existing = json.load(open(a.baseline))
            except Exception:
                existing = {}
        existing["koord_by_hour"] = kbh
        existing["n_by_hour"] = nbh
        existing["tod_cutoff"] = cutoff.isoformat()
        existing["tod_days"] = a.tod_days
        existing["tod_saved_at"] = now.isoformat()
        try:
            tmp = a.baseline + ".tmp"
            with open(tmp, "w") as f:
                json.dump(existing, f, indent=2, ensure_ascii=False)
            os.replace(tmp, a.baseline)
        except Exception as e:
            print(f"[zapis tod baseline fail: {e!r}]"); return 1
        print(f"[tod baseline wmergowany {a.baseline}] godzin={len(kbh)} cutoff={cutoff.isoformat()} days={a.tod_days}")
        print(json.dumps({h: f"{kbh[h]}% (n={nbh[h]})" for h in sorted(kbh, key=int)}, indent=2, ensure_ascii=False))
        return 0

    base = None
    if os.path.exists(a.baseline):
        try:
            base = json.load(open(a.baseline))
        except Exception as e:
            print(f"[baseline nieczytelny: {e!r}]")

    g = gates(cur, log, flags, base, since, now)
    stops = [x for x in g if x[1] == "STOP"]
    warns = [x for x in g if x[1] == "WARN"]
    overall = "🔴 STOP (rollback)" if stops else ("🟡 WARN" if warns else "🟢 GO")

    lines = []
    lines.append(f"# CANARY objm-lexr6 — {now.isoformat(timespec='seconds')} (okno {a.window_min} min)")
    _ro = len((log.get('reorder_oids') or set()) & (cur.get('shadow_oids') or set())) if cur.get('shadow_oids') else len(log.get('reorder_oids') or set())
    lines.append(f"SELECT={flags['select_on']} SHADOW={flags['shadow_on']} | decyzji {cur['n']} (sel {cur.get('n_sel', cur['n'])}, ord {cur.get('n_orders', cur['n'])}) | reorder {_ro} ord/{log['reorders']} linii | błędy {log['errors']}")
    lines.append(f"KOORD sel {cur.get('koord_pct_sel', cur['koord_pct'])}% (raw {cur['koord_pct']}%, early_bird {cur.get('koord_eb', 0)}) | ACK+ALERT {cur['ack_alert_pct']}% | AUTO {cur['auto_pct']}% | lat p50 {cur['lat_p50']} p95 {cur['lat_p95']} ms")
    if base:
        lines.append(f"baseline: KOORD {base.get('koord_pct')}% | ACK+ALERT {base.get('ack_alert_pct')}% | lat p95 {base.get('lat_p95')} ms")
        _exp = _expected_koord_tod(base, since, now)
        if _exp is not None:
            lines.append(f"oczek.(tod) KOORD dla pory dnia tego okna: {_exp:.1f}% (krzywa per-godzina SELECT-OFF, cutoff {str(base.get('tod_cutoff', '?'))[:16]})")
    else:
        lines.append("baseline: BRAK (uruchom --save-baseline przed flipem)")
    if cur["n"] < MIN_N_FOR_STOP:
        lines.append(f"⚠ próba n={cur['n']} < {MIN_N_FOR_STOP} → gate'y statystyczne wyciszone do INFO (off-peak/mała próba)")
    lines.append("")
    for name, st, det in g:
        mark = {"GO": "🟢", "STOP": "🔴", "WARN": "🟡", "INFO": "⚪"}.get(st, "·")
        lines.append(f"{mark} {name}: {st} — {det}")
    lines.append("")
    lines.append(f"## WERDYKT: {overall}")
    txt = "\n".join(lines)
    print(txt)

    if a.notify:
        # EDGE-TRIGGERED: alert tylko przy ZMIANIE werdyktu (+ rzadkie przypomnienie utrzymującego
        # się STOP/WARN), nie co tick. Stan czytany/zapisywany TYLKO przy --notify, żeby ręczny
        # read-only przebieg nie nadpisał stanu timera.
        prev = _load_notify_state()
        send, msg, new_state = _notify_decision(stops, warns, prev, now, timedelta(hours=NOTIFY_REMIND_H))
        if send:
            try:
                sys.path.insert(0, SCRIPTS)
                from dispatch_v2.telegram_utils import send_admin_alert
                send_admin_alert(msg, priority="low")
            except Exception as e:
                print(f"[notify pominięte: {e!r}]")
                new_state["last_sent"] = prev.get("last_sent")  # nie przesuwaj zegara → retry/remind zadziała
        try:
            _save_notify_state(new_state)
        except Exception as e:
            print(f"[zapis notify-state pominięty: {e!r}]")
    return 0


if __name__ == "__main__":
    sys.exit(main())
