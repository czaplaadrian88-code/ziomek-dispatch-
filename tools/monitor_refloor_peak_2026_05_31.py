#!/usr/bin/env python3
"""Monitor refloor (KROK 2) na peaku — czy kurier (domyślnie Bartek cid 123)
ma w apce aktualne czasy odbioru = ustalony czas_kuriera, a nie czas restauracji.

Co weryfikuje (na poziomie ŹRÓDŁA = courier_plans.json, czyli dokładnie tego,
czym rządzi refloor):
  dla każdego żywego odbioru (stop type==pickup) w planie kuriera porównuje
  plan.predicted_at vs orders_state[oid].czas_kuriera_warsaw.
    delta_min = czas_kuriera - predicted_at   (dodatnie = predicted WCZEŚNIEJ
    niż ustalono = objaw buga „czas restauracji").
  Werdykt per odbiór:
    OK             predicted >= czas_kuriera - 60s (refloor zadziałał lub plan był zgodny)
    PENDING_REFLOOR delta >= 60s (refloor podniesie na następnym ticku ≤5 min)
    NO_PROMISE     brak czas_kuriera (jeszcze nie ustalono — apka ma tylko czas restauracji)
  PENDING utrzymujące się ≥2 kolejne sample (>5 min) → STALE_BUG → alert.

WAŻNE: nawet PENDING_REFLOOR w planie nie psuje WYŚWIETLANIA — KROK 1 (clamp
w courier_api) i tak podnosi pokazany czas do czas_kuriera. Refloor naprawia
samo źródło (plan + dropoff kaskada). Monitor mierzy zbieżność źródła.

Dodatkowo: zlicza linie `PICKUP_REFLOOR cid=<cid>` w logach plan_recheck w oknie
(durable dowód że refloor realnie odpalał dla tego kuriera).

READ-ONLY: nie dotyka żadnych plików stanu, tylko czyta + Telegram.
"""
import argparse
import json
import re
import sys
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, "/root/.openclaw/workspace/scripts")

STATE = Path("/root/.openclaw/workspace/dispatch_state")
PLANS_PATH = STATE / "courier_plans.json"
ORDERS_PATH = STATE / "orders_state.json"
RECHECK_LOG = Path("/root/.openclaw/workspace/scripts/logs/plan_recheck.log")
REPORT_DIR = Path("/root/.openclaw/workspace/scripts/dispatch_v2/eod_drafts/2026-05-31")

OK_TOL_SEC = 60.0       # predicted w granicy 60s poniżej obietnicy = OK
STALE_AFTER_SAMPLES = 2  # PENDING utrzymujące się tyle sampli z rzędu = bug
WARSAW = ZoneInfo("Europe/Warsaw")  # DST-safe CET/CEST — L2 audyt 2.0 (był inline fixed +2)


def _send_tg(text: str) -> None:
    try:
        from dispatch_v2.telegram_utils import send_admin_alert
        send_admin_alert(text)
    except Exception as e:  # noqa: BLE001 — best-effort, monitor nie może paść na TG
        print(f"[tg-fail] {e}", file=sys.stderr)


def _parse_aware(iso):
    if not iso or not isinstance(iso, str):
        return None
    s = iso.strip()
    if not s:
        return None
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(s)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _hhmm_warsaw(dt):
    if dt is None:
        return "—"
    return dt.astimezone(WARSAW).strftime("%H:%M")


def _load_json(p):
    try:
        with open(p, "r", encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, json.JSONDecodeError):
        return {}


def evaluate_cid(cid: str) -> dict:
    plans = _load_json(PLANS_PATH)
    orders = _load_json(ORDERS_PATH)
    plan = plans.get(str(cid))
    out = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "cid": str(cid),
        "has_plan": plan is not None,
        "invalidated": None,
        "plan_version": None,
        "pickups": [],
    }
    if plan is None:
        return out
    out["invalidated"] = plan.get("invalidated_at")
    out["plan_version"] = plan.get("plan_version")
    if plan.get("invalidated_at") is not None:
        return out
    for s in plan.get("stops", []):
        if s.get("type") != "pickup":
            continue
        oid = str(s.get("order_id"))
        pred = _parse_aware(s.get("predicted_at"))
        rec = orders.get(oid)
        kur = rec.get("czas_kuriera_warsaw") if isinstance(rec, dict) else None
        floor = _parse_aware(kur)
        if floor is None:
            verdict = "NO_PROMISE"
            delta_min = None
        elif pred is None:
            verdict = "NO_PRED"
            delta_min = None
        else:
            delta_sec = (floor - pred).total_seconds()
            delta_min = round(delta_sec / 60.0, 2)
            verdict = "OK" if delta_sec <= OK_TOL_SEC else "PENDING_REFLOOR"
        out["pickups"].append({
            "oid": oid,
            "predicted_warsaw": _hhmm_warsaw(pred),
            "czas_kuriera_warsaw": _hhmm_warsaw(floor),
            "delta_min": delta_min,
            "verdict": verdict,
        })
    return out


def count_refloor_lines(cid: str, since: datetime):
    """Zwraca (count, [linie]) wystąpień PICKUP_REFLOOR dla cid od `since`."""
    if not RECHECK_LOG.exists():
        return 0, []
    pat_cid = re.compile(rf"PICKUP_REFLOOR cid={re.escape(str(cid))}\b")
    ts_pat = re.compile(r"^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})")
    hits = []
    try:
        with open(RECHECK_LOG, "r", encoding="utf-8", errors="replace") as fh:
            for line in fh:
                if "PICKUP_REFLOOR" not in line or not pat_cid.search(line):
                    continue
                m = ts_pat.match(line)
                if m:
                    try:
                        lt = datetime.strptime(m.group(1), "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
                        if lt < since:
                            continue
                    except ValueError:
                        pass
                hits.append(line.rstrip())
    except OSError:
        return 0, []
    return len(hits), hits[-20:]


def _fmt_pickups(pickups):
    if not pickups:
        return "   (brak żywych odbiorów)"
    rows = []
    for p in pickups:
        d = "" if p["delta_min"] is None else f" Δ={p['delta_min']:+.1f}min"
        rows.append(
            f"   oid={p['oid']} plan={p['predicted_warsaw']} "
            f"ustalono={p['czas_kuriera_warsaw']}{d} → {p['verdict']}"
        )
    return "\n".join(rows)


def run_watch(cid, until, interval, peak_start, label):
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    report_jsonl = REPORT_DIR / f"refloor_peak_{cid}_samples.jsonl"
    first_verdict_sent = False
    pending_streak = {}        # oid -> kolejne sample z PENDING
    stale_alerted = set()
    samples = []

    print(f"[watch] cid={cid} until={until.isoformat()} interval={interval}s label={label}")
    while True:
        ev = evaluate_cid(cid)
        live = [p for p in ev["pickups"] if p["verdict"] in ("OK", "PENDING_REFLOOR")]
        ev["live_pickup_count"] = len(live)
        samples.append(ev)
        with open(report_jsonl, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(ev, ensure_ascii=False) + "\n")

        # streak PENDING per oid
        cur_pending = {p["oid"] for p in ev["pickups"] if p["verdict"] == "PENDING_REFLOOR"}
        for oid in list(pending_streak):
            if oid not in cur_pending:
                pending_streak.pop(oid, None)
        for oid in cur_pending:
            pending_streak[oid] = pending_streak.get(oid, 0) + 1

        # pierwszy realny werdykt (Bartek ma żywe odbiory)
        if not first_verdict_sent and live:
            ok = [p for p in live if p["verdict"] == "OK"]
            bad = [p for p in live if p["verdict"] == "PENDING_REFLOOR"]
            head = "🟢" if not bad else "🟡"
            msg = (
                f"{head} REFLOOR MONITOR — pierwszy werdykt ({label})\n"
                f"Kurier cid={cid} ma {len(live)} żywych odbiorów teraz:\n"
                f"{_fmt_pickups(ev['pickups'])}\n\n"
                f"OK={len(ok)} PENDING_REFLOOR={len(bad)} "
                f"(PENDING zniknie na następnym ticku ≤5 min; clamp i tak "
                f"pokazuje w apce ustalony czas)."
            )
            _send_tg(msg)
            first_verdict_sent = True

        # alert STALE (PENDING >= 2 sample = >5 min, refloor nie podniósł)
        for oid, streak in pending_streak.items():
            if streak >= STALE_AFTER_SAMPLES and oid not in stale_alerted:
                pk = next((p for p in ev["pickups"] if p["oid"] == oid), {})
                _send_tg(
                    f"🔴 REFLOOR STALE ({label}) — cid={cid} oid={oid} odbiór "
                    f"w planie został na {pk.get('predicted_warsaw','?')} mimo "
                    f"ustalonego {pk.get('czas_kuriera_warsaw','?')} przez "
                    f"≥{streak} ticki. Refloor NIE podniósł — sprawdź "
                    f"ENABLE_PICKUP_REFLOOR / logi plan_recheck."
                )
                stale_alerted.add(oid)

        if datetime.now(timezone.utc) >= until:
            break
        time.sleep(interval)

    # DIGEST końcowy
    rc, rlines = count_refloor_lines(cid, peak_start)
    any_live = any(s.get("live_pickup_count", 0) > 0 for s in samples)
    all_pickups = [p for s in samples for p in s["pickups"]]
    n_ok = sum(1 for p in all_pickups if p["verdict"] == "OK")
    n_pending = sum(1 for p in all_pickups if p["verdict"] == "PENDING_REFLOOR")
    n_noprom = sum(1 for p in all_pickups if p["verdict"] == "NO_PROMISE")

    if not any_live:
        verdict = (
            f"⚪ REFLOOR DIGEST ({label}) — cid={cid}\n"
            f"Brak żywych odbiorów w oknie {_hhmm_warsaw(peak_start)}–"
            f"{_hhmm_warsaw(until)} ({len(samples)} sampli). Refloor nie miał "
            f"na czym działać (poprawny no-op). Refloor-fires w logu={rc}. "
            f"Powtórzę przy następnym peaku."
        )
    elif stale_alerted:
        verdict = (
            f"🔴 REFLOOR DIGEST ({label}) — cid={cid}\n"
            f"{len(stale_alerted)} odbiór(ów) został na czasie restauracji "
            f"mimo refloor (oid={sorted(stale_alerted)}). Sample OK={n_ok} "
            f"PENDING={n_pending}. Refloor-fires={rc}. WYMAGA pogłębienia."
        )
    else:
        verdict = (
            f"🟢 REFLOOR DIGEST ({label}) — cid={cid}\n"
            f"Wszystkie żywe odbiory Bartka pokazują ustalony czas kuriera "
            f"(źródło zbieżne). Sample: OK={n_ok} PENDING(przejściowe)="
            f"{n_pending} NO_PROMISE={n_noprom}. Refloor realnie podniósł czas "
            f"{rc}× w oknie. Apka = aktualna ✓"
        )

    _send_tg(verdict)

    md = REPORT_DIR / f"refloor_peak_{cid}_digest.md"
    with open(md, "a", encoding="utf-8") as fh:
        fh.write(f"\n## {label} — {datetime.now(timezone.utc).isoformat()}\n\n")
        fh.write(verdict + "\n\n")
        fh.write(f"- samples: {len(samples)}\n- refloor_lines: {rc}\n")
        for ln in rlines:
            fh.write(f"  - `{ln}`\n")
    print(verdict)
    return verdict


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--cid", default="123")
    ap.add_argument("--until-iso", default=None, help="UTC ISO koniec okna (wymagane w trybie watch)")
    ap.add_argument("--peak-start-iso", default=None, help="UTC ISO start okna (do liczenia refloor lines)")
    ap.add_argument("--interval-sec", type=int, default=300)
    ap.add_argument("--label", default="peak")
    ap.add_argument("--once", action="store_true", help="tylko jeden sample, druk, bez TG")
    args = ap.parse_args()

    if args.once:
        ev = evaluate_cid(args.cid)
        print(json.dumps(ev, ensure_ascii=False, indent=2))
        rc, rl = count_refloor_lines(args.cid, datetime.now(timezone.utc) - timedelta(hours=6))
        print(f"refloor_lines(6h)={rc}")
        for ln in rl:
            print("  ", ln)
        return

    until = _parse_aware(args.until_iso) if args.until_iso else None
    if until is None:
        print("watch wymaga --until-iso (UTC ISO)", file=sys.stderr)
        sys.exit(2)
    peak_start = _parse_aware(args.peak_start_iso) if args.peak_start_iso else datetime.now(timezone.utc)
    run_watch(args.cid, until, args.interval_sec, peak_start, args.label)


if __name__ == "__main__":
    main()
