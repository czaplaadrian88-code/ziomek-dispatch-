#!/usr/bin/env python3
"""eta_error_report — odczyt postępu kalibracji czasów Ziomka.

Czyta eta_calibration_log.jsonl i porównuje eta_error (delivered minus
obiecane) dla dnia docelowego vs baseline sprzed kalibracji tier-aware DWELL
(restart dispatch-shadow 2026-05-17 ~18:32 Warsaw). Raport na Telegram.

eta_error dodatni = dostawa później niż Ziomek obiecał = czas za krótki.

Uruchomienie:
    /root/.openclaw/venvs/dispatch/bin/python eta_error_report.py [YYYY-MM-DD]
Domyślnie dzień = dziś (Warsaw). Odpalane przez at-job (pon po lunchu).
"""
import json
import os
import sys
from datetime import datetime
from zoneinfo import ZoneInfo

sys.path.insert(0, "/root/.openclaw/workspace/scripts")

WARSAW = ZoneInfo("Europe/Warsaw")
BASE = "/root/.openclaw/workspace"
CAL_LOG = f"{BASE}/dispatch_state/eta_calibration_log.jsonl"
TIERS = f"{BASE}/dispatch_state/courier_tiers.json"

# Restart dispatch-shadow z tier-aware DWELL: 2026-05-17 16:32 UTC = 18:32 Warsaw.
DWELL_GO_LIVE = datetime(2026, 5, 17, 18, 32, tzinfo=WARSAW)


def _parse(s):
    if not s or not isinstance(s, str):
        return None
    try:
        if "T" in s:
            return datetime.fromisoformat(s.replace("Z", "+00:00"))
        return datetime.strptime(s.strip(), "%Y-%m-%d %H:%M:%S").replace(tzinfo=WARSAW)
    except (ValueError, TypeError):
        return None


def _stats(vals):
    """(n, mediana, średnia) — None gdy pusto."""
    if not vals:
        return (0, None, None)
    s = sorted(vals)
    return (len(s), s[len(s) // 2], sum(s) / len(s))


def _fmt(st):
    n, med, mean = st
    if n == 0:
        return "brak danych"
    return f"n={n:<4d} mediana={med:+5.1f}  średnia={mean:+5.1f}"


def load_tiers():
    try:
        d = json.load(open(TIERS))
    except Exception:
        return {}
    out = {}
    for cid, v in d.items():
        if isinstance(v, dict) and isinstance(v.get("bag"), dict):
            out[str(cid)] = v["bag"].get("tier")
    return out


def main():
    target = sys.argv[1] if len(sys.argv) > 1 else datetime.now(WARSAW).date().isoformat()
    tiers = load_tiers()

    baseline = []   # eta_error przed go-live tier-aware DWELL
    today = []      # wiersze z dnia docelowego (po kalibracji)
    rows_today = []
    for line in open(CAL_LOG, encoding="utf-8"):
        line = line.strip()
        if not line:
            continue
        try:
            r = json.loads(line)
        except (json.JSONDecodeError, ValueError):
            continue
        err = r.get("eta_error_min")
        deliv = _parse(r.get("delivered_at"))
        # Tylko wiersze z dopasowanym realnym kurierem — jedyna wiarygodna
        # metryka (logger v2). Czasówki pomijamy.
        if err is None or deliv is None or not r.get("matched_courier"):
            continue
        if r.get("was_czasowka"):
            continue
        if deliv < DWELL_GO_LIVE:
            baseline.append(r)
        if str(r.get("delivered_at", ""))[:10] == target:
            today.append(r)
            rows_today.append(r)

    def by_bucket(rows, b):
        return [r["eta_error_min"] for r in rows if r.get("bucket") == b]

    lines = [f"📊 Kalibracja czasów — odczyt {target}", ""]
    lines.append("eta_error = dostawa minus czas obiecany przez Ziomka.")
    lines.append("Dodatni = za krótko. Cel: zbliżyć do zera.")
    lines.append("")
    lines.append("PER BUCKET — baseline (przed) → dzień docelowy:")
    for b in ("peak", "shoulder", "offpeak"):
        bn, bmed, _ = _stats(by_bucket(baseline, b))
        tn, tmed, _ = _stats(by_bucket(today, b))
        if bn == 0 and tn == 0:
            continue
        bm = f"{bmed:+.1f}" if bmed is not None else "—"
        tm = f"{tmed:+.1f}" if tmed is not None else "—"
        delta = f"  Δ={tmed - bmed:+.1f}" if (bmed is not None and tmed is not None) else ""
        lines.append(f"  {b:9s} {bm:>6s} → {tm:>6s} (n={tn}){delta}")

    lines.append("")
    lines.append(f"DZIEŃ {target} — solo vs bundle:")
    solo = [r["eta_error_min"] for r in today if r.get("bag_size") == 1]
    bund = [r["eta_error_min"] for r in today if r.get("bag_size") and r["bag_size"] >= 2]
    lines.append(f"  solo:   {_fmt(_stats(solo))}")
    lines.append(f"  bundle: {_fmt(_stats(bund))}")

    lines.append("")
    lines.append(f"DZIEŃ {target} — per tier kuriera:")
    per_tier = {}
    for r in today:
        t = tiers.get(str(r.get("real_courier_id"))) or "nieznany"
        per_tier.setdefault(t, []).append(r["eta_error_min"])
    for t in ("gold", "std+", "std", "slow", "new", "nieznany"):
        if t in per_tier:
            lines.append(f"  {t:9s} {_fmt(_stats(per_tier[t]))}")

    # Werdykt — porównanie mediany peak.
    bn, bmed, _ = _stats(by_bucket(baseline, "peak"))
    tn, tmed, _ = _stats(by_bucket(today, "peak"))
    lines.append("")
    if tn < 15:
        lines.append(f"⏳ Za mało danych dziennych (peak n={tn}) — odczyt orientacyjny.")
    elif bmed is not None and tmed is not None:
        d = tmed - bmed
        if d <= -1.5:
            lines.append(f"✅ Luka się domyka — peak {bmed:+.1f} → {tmed:+.1f} ({d:+.1f} min).")
        elif d >= 1.5:
            lines.append(f"⚠️ Luka rośnie — peak {bmed:+.1f} → {tmed:+.1f} ({d:+.1f} min).")
        else:
            lines.append(f"➖ Bez istotnej zmiany w peak ({d:+.1f} min) — DWELL to ~3-5 min z luki, reszta = Sprint 2 (mnożniki + narzut).")

    msg = "\n".join(lines)
    print(msg)
    if os.environ.get("ETA_REPORT_NO_SEND") == "1":
        print("\n[ETA_REPORT_NO_SEND=1 — pominięto wysyłkę Telegram]")
        return 0
    try:
        from dispatch_v2.telegram_utils import send_admin_alert
        ok = send_admin_alert(msg)
        print(f"\ntelegram send_admin_alert={ok}")
    except Exception as e:  # noqa: BLE001
        print(f"\ntelegram fail: {type(e).__name__}: {e}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
