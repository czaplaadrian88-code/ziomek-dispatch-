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
"""
import json, os, sys, glob, gzip, argparse
from datetime import datetime, timezone, timedelta

SCRIPTS = "/root/.openclaw/workspace/scripts"
SHADOW = f"{SCRIPTS}/logs/shadow_decisions.jsonl"
LOGS = [f"{SCRIPTS}/logs/dispatch.log", f"{SCRIPTS}/logs/watcher.log"]
FLAGS = f"{SCRIPTS}/flags.json"
BASELINE_DEFAULT = "/root/.openclaw/workspace/dispatch_state/objm_lexr6_canary_baseline.json"

# Progi gate'ów (env-overridable; KANON = plan, Adrian potwierdza domenę)
KOORD_STOP_PP   = float(os.environ.get("CANARY_KOORD_STOP_PP", "5.0"))
ACKALERT_STOP_PP= float(os.environ.get("CANARY_ACKALERT_STOP_PP", "8.0"))
LAT_P95_STOP_PCT= float(os.environ.get("CANARY_LAT_P95_STOP_PCT", "15.0"))
REORDER_LO      = float(os.environ.get("CANARY_REORDER_LO_PCT", "5.0"))
REORDER_HI      = float(os.environ.get("CANARY_REORDER_HI_PCT", "25.0"))
# MIN-SAMPLE: poniżej tylu decyzji w oknie gate'y STATYSTYCZNE degradujemy STOP/WARN→INFO
# (off-peak n jest strukturalnie maleńkie → szum). G1-błędy (pick failed) NIGDY nie wyciszane.
MIN_N_FOR_STOP    = int(os.environ.get("CANARY_MIN_N_FOR_STOP", "30"))
TOD_BASELINE_DAYS = int(os.environ.get("CANARY_TOD_DAYS", "7"))
_SUPPRESSIBLE_UNDER_MIN_N = {"G2a-KOORD", "G2b-auto-route", "G2c-reorder", "G1-latencja"}


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


def shadow_metrics(since):
    n = koord = 0
    auto = {"AUTO": 0, "ACK": 0, "ALERT": 0}
    lats = []
    if not os.path.exists(SHADOW):
        return None
    for line in _rot_lines(SHADOW, since):
        if not line.strip():
            continue
        try:
            r = json.loads(line)
        except Exception:
            continue
        t = _parse_iso(r.get("ts"))
        if t is None or t < since:
            continue
        n += 1
        if str(r.get("verdict")) == "KOORD":
            koord += 1
        a = str(r.get("auto_route") or "")
        if a in auto:
            auto[a] += 1
        lm = r.get("latency_ms")
        if isinstance(lm, (int, float)):
            lats.append(float(lm))
    return {
        "n": n,
        "koord_pct": round(_pct(koord, n), 2),
        "ack_alert_pct": round(_pct(auto["ACK"] + auto["ALERT"], n), 2),
        "auto_pct": round(_pct(auto["AUTO"], n), 2),
        "lat_p50": _pctile(lats, 0.50),
        "lat_p95": _pctile(lats, 0.95),
    }


def log_signals(since):
    reorders = errors = 0
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
    return {"reorders": reorders, "errors": errors}


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
    agg = defaultdict(lambda: [0, 0])  # hour -> [n, koord]
    start = cutoff - timedelta(days=days)
    if not os.path.exists(SHADOW):
        return {}, {}
    for line in _rot_lines(SHADOW, start):
        line = line.strip()
        if not line:
            continue
        try:
            r = json.loads(line)
        except Exception:
            continue
        t = _parse_iso(r.get("ts"))
        if t is None or t < start or t >= cutoff:
            continue
        a = agg[t.hour]
        a[0] += 1
        if str(r.get("verdict")) == "KOORD":
            a[1] += 1
    koord_by_hour, n_by_hour = {}, {}
    for h, (n, k) in agg.items():
        if n <= 0:
            continue
        n_by_hour[str(h)] = n
        koord_by_hour[str(h)] = round(_pct(k, n), 2)
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
    reorder_pct = _pct(log["reorders"], n) if n else 0.0

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

    # G2a KOORD — time-of-day aware: porównaj do OCZEKIWANEGO KOORD% dla pory dnia okna
    # (krzywa per-godzina SELECT-OFF). Usuwa confound „poranny off-peak vs peakowy baseline".
    # Próg +5pp (KANON planu) zachowany — wykrywa REALNY wzrost ponad normę tej pory dnia.
    exp_tod = _expected_koord_tod(base, since, now)
    if exp_tod is not None:
        d = cur["koord_pct"] - exp_tod
        st = "STOP" if d > KOORD_STOP_PP else "GO"
        out.append(("G2a-KOORD", st, f"{cur['koord_pct']:.1f}% vs oczek.(tod) {exp_tod:.1f}% (Δ{d:+.1f}pp, limit +{KOORD_STOP_PP:.0f})"))
    elif base and base.get("koord_pct") is not None:
        d = cur["koord_pct"] - base["koord_pct"]
        st = "STOP" if d > KOORD_STOP_PP else "GO"
        out.append(("G2a-KOORD", st, f"{cur['koord_pct']:.1f}% vs baseline {base['koord_pct']:.1f}% (Δ{d:+.1f}pp, limit +{KOORD_STOP_PP:.0f}) [flat — brak krzywej tod]"))
    else:
        out.append(("G2a-KOORD", "INFO", f"{cur['koord_pct']:.1f}% (brak baseline)"))

    # G2b auto-route
    if base and base.get("ack_alert_pct") is not None:
        d = cur["ack_alert_pct"] - base["ack_alert_pct"]
        st = "STOP" if d > ACKALERT_STOP_PP else "GO"
        out.append(("G2b-auto-route", st, f"ACK+ALERT {cur['ack_alert_pct']:.1f}% vs {base['ack_alert_pct']:.1f}% (Δ{d:+.1f}pp, limit +{ACKALERT_STOP_PP:.0f}); AUTO {cur['auto_pct']:.1f}%"))
    else:
        out.append(("G2b-auto-route", "INFO", f"ACK+ALERT {cur['ack_alert_pct']:.1f}% / AUTO {cur['auto_pct']:.1f}% (brak baseline)"))

    # G2c reorder sanity (tylko gdy SELECT faktycznie ON)
    if flags.get("select_on"):
        if reorder_pct < REORDER_LO or reorder_pct > REORDER_HI:
            out.append(("G2c-reorder", "WARN", f"{reorder_pct:.1f}% (oczek. ~12%, pas {REORDER_LO:.0f}-{REORDER_HI:.0f}%) — {log['reorders']}/{n}"))
        else:
            out.append(("G2c-reorder", "GO", f"{reorder_pct:.1f}% ({log['reorders']}/{n})"))
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
        snap = {k: cur[k] for k in cur}
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
    lines.append(f"SELECT={flags['select_on']} SHADOW={flags['shadow_on']} | decyzji {cur['n']} | reorder {log['reorders']} | błędy {log['errors']}")
    lines.append(f"KOORD {cur['koord_pct']}% | ACK+ALERT {cur['ack_alert_pct']}% | AUTO {cur['auto_pct']}% | lat p50 {cur['lat_p50']} p95 {cur['lat_p95']} ms")
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

    if a.notify and (stops or warns):
        try:
            sys.path.insert(0, SCRIPTS)
            from dispatch_v2.telegram_utils import send_admin_alert
            head = "🔴 CANARY objm-lexr6 STOP" if stops else "🟡 CANARY objm-lexr6 WARN"
            send_admin_alert(head + " | " + "; ".join(f"{n}:{s}" for n, s, _ in (stops or warns)), priority="low")
        except Exception as e:
            print(f"[notify pominięte: {e!r}]")
    return 0


if __name__ == "__main__":
    sys.exit(main())
