"""carried_first_peak_monitor — READ-ONLY monitor regresji kolejności na peak 2026-06-24.

Sprawdza że fix recanon-on-write + opcja A TRZYMA: niesione (picked_up) jedzenie jest na
froncie serwowanej trasy GDY POWINNO. Odróżnia REGRESJĘ od legalnego relaxu:
  - REGRESJA = STARE niesione (wiek >SOFT_MAX=20 min, relax NIE może go cofnąć) NIE na froncie
    na planie POKRYWAJĄCYM+ważnym (TRUST renderuje kanon verbatim) — to był pierwotny bug.
  - OK = świeże niesione (≤20 min) pickup-first (relax „odbierz po drodze", Mateusz) /
    plan invalidated lub niepokrywający (panel opcja A / fallback floor-uje z definicji).

Czyta TYLKO courier_plans.json + orders_state.json (dispatch_state). Append `carried_first_monitor.jsonl`.
Alert Telegram (send_admin_alert) TYLKO przy regresji, z cooldownem. `--summary` = werdykt dnia.
"""
import os
import sys
import json
import logging
from datetime import datetime, timezone, timedelta
from zoneinfo import ZoneInfo

sys.path.insert(0, "/root/.openclaw/workspace/scripts")
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] carried_monitor: %(message)s")
_log = logging.getLogger("carried_monitor")

WARSAW = ZoneInfo("Europe/Warsaw")
STATE_DIR = "/root/.openclaw/workspace/dispatch_state"
ORDERS_STATE = f"{STATE_DIR}/orders_state.json"
PLANS = f"{STATE_DIR}/courier_plans.json"
OUT_JSONL = f"{STATE_DIR}/carried_first_monitor.jsonl"
COOLDOWN_PATH = f"{STATE_DIR}/carried_first_monitor_cooldown.json"
WATCHER_LOG = "/root/.openclaw/workspace/scripts/logs/watcher.log"   # tu loguje recanon (panel_watcher)
SOFT_MAX_MIN = 20.0           # relax nie cofa carried starszego niż tyle → musi być floor-owane
COOLDOWN_MIN = 30
ACTIVE = {"assigned", "picked_up"}


def _load(p, d=None):
    try:
        with open(p) as fh:
            return json.load(fh)
    except Exception:
        return {} if d is None else d


def _parse(ts):
    if not ts or ts == "None":
        return None
    try:
        dt = datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
        return (dt if dt.tzinfo else dt.replace(tzinfo=WARSAW)).astimezone(timezone.utc)
    except Exception:
        return None


def _check(now):
    ds = _load(ORDERS_STATE)
    plans = _load(PLANS)
    by_cid = {}
    for oid, o in ds.items():
        if isinstance(o, dict) and o.get("status") in ACTIVE and o.get("courier_id") is not None:
            by_cid.setdefault(str(o["courier_id"]), []).append(str(oid))

    regressions = []
    checked = relax_ok = invalid_fallback = 0
    for cid, oids in by_cid.items():
        carried = [o for o in oids if (ds.get(o) or {}).get("status") == "picked_up"]
        if not carried:
            continue
        plan = plans.get(cid) or {}
        stops = [s for s in (plan.get("stops") or []) if isinstance(s, dict)]
        if not stops or plan.get("invalidated_at"):
            invalid_fallback += 1        # opcja A / fallback floor-uje z definicji
            continue
        covered = {str(s.get("order_id")) for s in stops}
        if not (set(oids) <= covered):
            invalid_fallback += 1        # niepokrywający → fallback floor-uje
            continue
        # plan pokrywa+ważny → TRUST renderuje TĘ kolejność. Sprawdź STARE niesione.
        checked += 1
        # zbuduj serwowaną kolejność (jak _order_from_plan_seq: picked_up = sam dropoff)
        served = []
        seen = set()
        for s in stops:
            oid = str(s.get("order_id"))
            if oid not in set(oids):
                continue
            typ = "pickup" if s.get("type") == "pickup" else "dropoff"
            if typ == "pickup" and (ds.get(oid) or {}).get("status") == "picked_up":
                continue
            if (typ, oid) in seen:
                continue
            seen.add((typ, oid))
            served.append((typ, oid))
        for oid in carried:
            pa = _parse((ds.get(oid) or {}).get("picked_up_at"))
            age = (now - pa).total_seconds() / 60.0 if pa else None
            if age is None or age <= SOFT_MAX_MIN:
                relax_ok += 1            # świeże → relax dopuszczalny, nie regresja
                continue
            # STARE niesione: czy coś NIE-carried-dropoff stoi PRZED nim?
            pos = next((i for i, (t, o) in enumerate(served) if t == "dropoff" and o == oid), None)
            if pos is None:
                continue
            before_bad = any(not (t == "dropoff" and o in carried) for (t, o) in served[:pos])
            if before_bad:
                regressions.append({"cid": cid, "oid": oid, "age_min": round(age, 1),
                                    "served": served, "rest": (ds.get(oid) or {}).get("restaurant")})
    return {"checked_covering": checked, "relax_ok_fresh": relax_ok,
            "invalid_fallback": invalid_fallback, "n_couriers_carried": sum(1 for c in by_cid.values() if any((ds.get(o) or {}).get("status") == "picked_up" for o in c)),
            "regressions": regressions}


def _recanon_count_recent(since_min=12):
    """Ile RECANON_ON_ w dispatch.log w ostatnich ~since_min (potwierdza że mechanizm żyje)."""
    try:
        cutoff = (datetime.now(timezone.utc) - timedelta(minutes=since_min)).strftime("%Y-%m-%d %H:%M")
        n = 0
        with open(WATCHER_LOG, "rb") as fh:
            fh.seek(max(0, fh.seek(0, 2) - 300_000))
            for line in fh.read().decode("utf-8", "replace").splitlines():
                if "RECANON_ON_" in line and line[:16] >= cutoff:
                    n += 1
        return n
    except Exception:
        return None


def _append(row):
    try:
        with open(OUT_JSONL, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")
            fh.flush()
            os.fsync(fh.fileno())
    except Exception as e:
        _log.warning(f"append fail: {e}")


def _tg(msg, source):
    try:
        from dispatch_v2.telegram_utils import send_admin_alert
        send_admin_alert(msg, source=source)
        return True
    except Exception as e:
        _log.warning(f"telegram fail: {type(e).__name__}: {e}")
        return False


def run_check():
    now = datetime.now(timezone.utc)
    res = _check(now)
    res["recanon_recent"] = _recanon_count_recent()
    res["ts"] = now.isoformat()
    _append(res)
    regs = res["regressions"]
    _log.info(f"checked_covering={res['checked_covering']} regressions={len(regs)} "
              f"relax_ok={res['relax_ok_fresh']} invalid_fallback={res['invalid_fallback']} "
              f"recanon_recent={res['recanon_recent']}")
    if regs:
        cd = _load(COOLDOWN_PATH)
        keys = {f"{r['cid']}:{r['oid']}" for r in regs}
        last = _parse(cd.get("last_alert"))
        recent = last is not None and (now - last).total_seconds() / 60.0 < COOLDOWN_MIN
        if not recent:
            lines = ["⚠️ REGRESJA KOLEJNOŚCI (peak monitor 24.06): STARE niesione NIE na froncie!"]
            for r in regs[:6]:
                lines.append(f"  cid {r['cid']} oid {r['oid']} ({r['rest']}) wiek {r['age_min']} min — #1={r['served'][0] if r['served'] else '?'}")
            lines.append("Sprawdź recanon (dispatch-panel-watcher) + opcja A. Detal: carried_first_monitor.jsonl")
            _tg("\n".join(lines), "carried_first_peak_monitor")
            cd["last_alert"] = now.isoformat()
            cd["keys"] = sorted(keys)
            try:
                with open(COOLDOWN_PATH, "w") as fh:
                    json.dump(cd, fh)
            except Exception:
                pass
    return res


def run_summary():
    rows = []
    try:
        with open(OUT_JSONL) as fh:
            for line in fh:
                line = line.strip()
                if line:
                    rows.append(json.loads(line))
    except Exception:
        pass
    today = datetime.now(WARSAW).strftime("%Y-%m-%d")
    rows = [r for r in rows if str(r.get("ts", ""))[:10] in (today, datetime.now(timezone.utc).strftime("%Y-%m-%d"))]
    total_checks = len(rows)
    total_reg = sum(len(r.get("regressions") or []) for r in rows)
    ticks_with_reg = sum(1 for r in rows if r.get("regressions"))
    max_checked = max((r.get("checked_covering", 0) for r in rows), default=0)
    recanon_seen = any((r.get("recanon_recent") or 0) > 0 for r in rows)
    if total_reg == 0:
        verdict = "✅ KOLEJNOŚĆ TRZYMA — 0 regresji w peaku (stare niesione zawsze na froncie)."
    else:
        verdict = f"⚠️ {total_reg} regresji w {ticks_with_reg} tickach — recanon/opcja A nie domyka wszystkiego, sprawdź."
    msg = (f"🔎 PEAK MONITOR kolejności 24.06 — podsumowanie\n"
           f"Tików: {total_checks} · plany pokrywające sprawdzone (max/tick): {max_checked}\n"
           f"Regresji łącznie: {total_reg} (tików z regresją: {ticks_with_reg})\n"
           f"recanon aktywny w peaku: {'TAK' if recanon_seen else '— (brak w oknach próbek; werdykt i tak na regresjach)'}\n"
           f"➤ {verdict}")
    print(msg)
    if "--no-telegram" not in sys.argv:
        _tg(msg, "carried_first_peak_review")


def main():
    if "--summary" in sys.argv:
        run_summary()
    else:
        run_check()


if __name__ == "__main__":
    main()
