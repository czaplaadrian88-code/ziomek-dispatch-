#!/usr/bin/env python3
"""eta_error_report — odczyt postępu kalibracji czasów Ziomka.

Czyta eta_calibration_log.jsonl i porównuje eta_error (delivered minus
obiecane) dla dnia docelowego vs baseline sprzed kalibracji tier-aware DWELL
(restart dispatch-shadow 2026-05-17 ~18:32 Warsaw). Raport na Telegram.

eta_error dodatni = dostawa później niż Ziomek obiecał = czas za krótki.

Uruchomienie:
    eta_error_report.py                          → dziś (Warsaw)
    eta_error_report.py YYYY-MM-DD               → pojedynczy dzień
    eta_error_report.py YYYY-MM-DD YYYY-MM-DD    → zakres [start, end] inclusive
Tryb zakresu (2026-05-18) — kumuluje wiersze z wielu dni dla grubszej próby
(kalibracja DRIVE_SPEED_MULT wymaga peak n≥40). Odpalane przez at-job.
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


def _parse_args(argv):
    """argv[1:] → (start, end) ISO daty inclusive. 0 arg=dziś, 1=single, 2=zakres."""
    args = argv[1:]
    if len(args) >= 2:
        start, end = sorted([args[0], args[1]])
    elif len(args) == 1:
        start = end = args[0]
    else:
        start = end = datetime.now(WARSAW).date().isoformat()
    return start, end


def main():
    start, end = _parse_args(sys.argv)
    is_range = start != end
    period_label = start if not is_range else f"{start}..{end}"
    span_word = "OKRES" if is_range else "DZIEŃ"
    tiers = load_tiers()

    baseline = []   # eta_error przed go-live tier-aware DWELL
    period = []     # wiersze z okresu docelowego (po kalibracji)
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
        # Zakres inclusive; single-day = przypadek start==end. Period = TYLKO
        # post-DWELL-go-live — wiersze sprzed go-live należą do baseline, nie do
        # „docelowego" (istotne gdy zakres obejmuje 17.05, dzień go-live).
        _dday = str(r.get("delivered_at", ""))[:10]
        if start <= _dday <= end and deliv >= DWELL_GO_LIVE:
            period.append(r)

    def by_bucket(rows, b):
        return [r["eta_error_min"] for r in rows if r.get("bucket") == b]

    lines = [f"📊 Kalibracja czasów — odczyt {period_label}", ""]
    lines.append("eta_error = dostawa minus czas obiecany przez Ziomka.")
    lines.append("Dodatni = za krótko. Cel: zbliżyć do zera.")
    if is_range:
        lines.append(f"(zakres kumulatywny {start} → {end})")
    lines.append("")
    lines.append("PER BUCKET — baseline (przed) → docelowy:")
    for b in ("peak", "shoulder", "offpeak"):
        bn, bmed, _ = _stats(by_bucket(baseline, b))
        tn, tmed, _ = _stats(by_bucket(period, b))
        if bn == 0 and tn == 0:
            continue
        bm = f"{bmed:+.1f}" if bmed is not None else "—"
        tm = f"{tmed:+.1f}" if tmed is not None else "—"
        delta = f"  Δ={tmed - bmed:+.1f}" if (bmed is not None and tmed is not None) else ""
        lines.append(f"  {b:9s} {bm:>6s} → {tm:>6s} (n={tn}){delta}")

    lines.append("")
    lines.append(f"{span_word} {period_label} — solo vs bundle:")
    solo = [r["eta_error_min"] for r in period if r.get("bag_size") == 1]
    bund = [r["eta_error_min"] for r in period if r.get("bag_size") and r["bag_size"] >= 2]
    lines.append(f"  solo:   {_fmt(_stats(solo))}")
    lines.append(f"  bundle: {_fmt(_stats(bund))}")

    lines.append("")
    lines.append(f"{span_word} {period_label} — per tier kuriera:")
    per_tier = {}
    for r in period:
        t = tiers.get(str(r.get("real_courier_id"))) or "nieznany"
        per_tier.setdefault(t, []).append(r["eta_error_min"])
    for t in ("gold", "std+", "std", "slow", "new", "nieznany"):
        if t in per_tier:
            lines.append(f"  {t:9s} {_fmt(_stats(per_tier[t]))}")

    # Werdykt — porównanie mediany peak. Próg „solidny" dla kalibracji = peak n≥40.
    bn, bmed, _ = _stats(by_bucket(baseline, "peak"))
    tn, tmed, _ = _stats(by_bucket(period, "peak"))
    lines.append("")
    if tn < 40:
        lines.append(f"⏳ Za mało danych (peak n={tn}, próg kalibracji n≥40) — odczyt orientacyjny.")
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
