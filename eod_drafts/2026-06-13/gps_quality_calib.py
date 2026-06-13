#!/usr/bin/env python3
"""GPS-02 — harness kalibracyjny progów filtra jakości GPS (accuracy + teleport).

Dwa wejścia danych:
  1. courier_api.db / gps_history — historia fixów (accuracy + inter-fix speed).
     Pozwala oszacować rozkład PRZED jakąkolwiek decyzją (ile fixów odpadłoby
     przy danym progu accuracy / teleport).
  2. dispatch_state/gps_quality_shadow.jsonl — żywy shadow log z produkcji
     (werdykty filtra; available dopiero po wdrożeniu shadow + ruchu GPS).

Cel: dobrać GPS_ACCURACY_MAX_M / GPS_TELEPORT_* na DANYCH, nie na oko, PRZED
flipem ENABLE_GPS_ACCURACY_TELEPORT_FILTER. Read-only (nic nie zapisuje poza
opcjonalnym raportem .md przez --md).

Uwaga (korekta Adriana 13.06): brak GPS = celowa polityka, NIE liczymy go jako
"odrzucony". Harness mierzy tylko jakość REALNYCH fixów.

Uruchom:
    /root/.openclaw/venvs/dispatch/bin/python \
        dispatch_v2/eod_drafts/2026-06-13/gps_quality_calib.py [--md OUT.md]
"""
import argparse
import json
import math
import os
import sqlite3
import sys
from collections import defaultdict

GPS_DB = "/root/.openclaw/workspace/dispatch_state/courier_api.db"
SHADOW_LOG = "/root/.openclaw/workspace/dispatch_state/gps_quality_shadow.jsonl"

ACCURACY_THRESHOLDS = [50, 75, 100, 120, 150, 200, 300, 500]
# (min_jump_km, max_speed_kmh) warianty teleportu
TELEPORT_VARIANTS = [(2.0, 120.0), (2.0, 150.0), (1.5, 120.0), (3.0, 120.0), (1.0, 150.0)]
TELEPORT_MIN_DT_S = 3.0
TELEPORT_ANCHOR_MAX_AGE_MIN = 8.0
GAP_MAX_S = 600  # par fixów z dt > 10 min pomijamy (nie inter-fix)


def _hav_km(a, b):
    R = 6371.0
    lat1, lon1 = a
    lat2, lon2 = b
    p1 = math.radians(lat1)
    p2 = math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lon2 - lon1)
    x = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * R * math.asin(min(1.0, math.sqrt(x)))


def _pct(n, d):
    return 100.0 * n / d if d else 0.0


def analyze_history(db_path):
    out = {"ok": False}
    if not os.path.exists(db_path):
        out["err"] = f"brak {db_path}"
        return out
    c = sqlite3.connect(db_path)
    cur = c.cursor()

    # ── ACCURACY ──
    cur.execute("SELECT COUNT(*) FROM gps_history WHERE accuracy IS NOT NULL")
    acc_tot = cur.fetchone()[0]
    cur.execute("SELECT MIN(accuracy),MAX(accuracy),AVG(accuracy) FROM gps_history WHERE accuracy IS NOT NULL")
    amin, amax, aavg = cur.fetchone()
    cur.execute("SELECT COUNT(*) FROM gps_history WHERE accuracy IS NULL")
    acc_missing = cur.fetchone()[0]
    acc_rows = []
    for thr in ACCURACY_THRESHOLDS:
        cur.execute("SELECT COUNT(*) FROM gps_history WHERE accuracy > ?", (thr,))
        over = cur.fetchone()[0]
        acc_rows.append((thr, over, _pct(over, acc_tot)))

    # ── TELEPORT (inter-fix) ──
    cur.execute(
        "SELECT courier_id, lat, lon, recorded_at FROM gps_history "
        "WHERE recorded_at IS NOT NULL AND lat IS NOT NULL AND lon IS NOT NULL "
        "ORDER BY courier_id, recorded_at"
    )
    per = defaultdict(list)
    for cid, lat, lon, ts in cur.fetchall():
        per[cid].append((ts, lat, lon))
    pairs = 0
    maxspeed = 0.0
    # liczymy ile par WPADŁOBY w teleport per wariant
    variant_hits = {v: 0 for v in TELEPORT_VARIANTS}
    speed_buckets = {">200": 0, "120-200": 0, "80-120": 0, "<=80": 0}
    for cid, seq in per.items():
        for i in range(1, len(seq)):
            t0, la0, lo0 = seq[i - 1]
            t1, la1, lo1 = seq[i]
            dt = t1 - t0
            if dt <= 0 or dt > GAP_MAX_S:
                continue
            jump = _hav_km((la0, lo0), (la1, lo1))
            if dt < TELEPORT_MIN_DT_S:
                # prędkości nie liczymy (jak w gps_quality) — par nie wlicza do speed buckets
                # ale wciąż liczymy do pairs total (telemetria)
                pairs += 1
                continue
            if (dt / 60.0) > TELEPORT_ANCHOR_MAX_AGE_MIN:
                pairs += 1
                continue
            spd = jump / (dt / 3600.0)
            pairs += 1
            maxspeed = max(maxspeed, spd)
            if spd > 200:
                speed_buckets[">200"] += 1
            elif spd > 120:
                speed_buckets["120-200"] += 1
            elif spd > 80:
                speed_buckets["80-120"] += 1
            else:
                speed_buckets["<=80"] += 1
            for (mj, ms) in TELEPORT_VARIANTS:
                if jump > mj and spd > ms:
                    variant_hits[(mj, ms)] += 1

    out.update({
        "ok": True,
        "acc_tot": acc_tot, "acc_missing": acc_missing,
        "acc_min": amin, "acc_max": amax, "acc_avg": aavg,
        "acc_rows": acc_rows,
        "pairs": pairs, "maxspeed": maxspeed,
        "speed_buckets": speed_buckets,
        "variant_hits": [(v, variant_hits[v], _pct(variant_hits[v], pairs)) for v in TELEPORT_VARIANTS],
    })
    return out


def analyze_shadow(log_path):
    out = {"ok": False}
    if not os.path.exists(log_path):
        out["err"] = f"brak {log_path} (shadow jeszcze nie zebrał danych)"
        return out
    n = 0
    low_acc = 0
    teleport = 0
    no_acc = 0
    reject = 0
    active = 0
    accs = []
    for line in open(log_path, "rb"):
        try:
            d = json.loads(line)
        except Exception:
            continue
        n += 1
        if d.get("low_accuracy"):
            low_acc += 1
        if d.get("teleport"):
            teleport += 1
        if not d.get("has_accuracy_field"):
            no_acc += 1
        if not d.get("accept"):
            reject += 1
        if d.get("filter_active"):
            active += 1
        if d.get("accuracy_m") is not None:
            accs.append(d["accuracy_m"])
    out.update({
        "ok": True, "n": n, "low_acc": low_acc, "teleport": teleport,
        "no_acc": no_acc, "reject": reject, "active": active,
        "acc_p50": sorted(accs)[len(accs) // 2] if accs else None,
    })
    return out


def render(hist, shadow):
    L = []
    L.append("# GPS-02 — raport kalibracyjny progów filtra jakości GPS\n")
    L.append("## 1. Historia gps_history (rozkład PRZED decyzją)\n")
    if not hist.get("ok"):
        L.append(f"- ⚠ {hist.get('err')}\n")
    else:
        L.append(f"- fixy z accuracy: **{hist['acc_tot']}** (brak pola accuracy: {hist['acc_missing']})")
        L.append(f"- accuracy min/max/avg: {hist['acc_min']:.1f} / {hist['acc_max']:.1f} / {hist['acc_avg']:.1f} m\n")
        L.append("| próg accuracy (m) | fixów > próg | % |")
        L.append("|---|---|---|")
        for thr, over, pct in hist["acc_rows"]:
            mark = " ⟵ LIVE" if thr == 150 else ""
            L.append(f"| {thr}{mark} | {over} | {pct:.2f}% |")
        L.append("")
        L.append(f"- par inter-fix (dt≤10min): **{hist['pairs']}**, max prędkość {hist['maxspeed']:.0f} km/h")
        sb = hist["speed_buckets"]
        tot = hist["pairs"] or 1
        L.append(f"- rozkład prędkości: >200={_pct(sb['>200'],tot):.3f}% / 120-200={_pct(sb['120-200'],tot):.3f}% "
                 f"/ 80-120={_pct(sb['80-120'],tot):.3f}% / ≤80={_pct(sb['<=80'],tot):.2f}%\n")
        L.append("| wariant teleportu (jump_km, speed_kmh) | trafień | % par |")
        L.append("|---|---|---|")
        for (v, hits, pct) in hist["variant_hits"]:
            mark = " ⟵ LIVE" if v == (2.0, 120.0) else ""
            L.append(f"| jump>{v[0]} & speed>{v[1]}{mark} | {hits} | {pct:.3f}% |")
        L.append("")
    L.append("## 2. Shadow log gps_quality_shadow.jsonl (żywe werdykty)\n")
    if not shadow.get("ok"):
        L.append(f"- ⚠ {shadow.get('err')}\n")
    else:
        s = shadow
        L.append(f"- wpisów: **{s['n']}** (filter_active={s['active']})")
        L.append(f"- low_accuracy: {s['low_acc']} ({_pct(s['low_acc'],s['n']):.2f}%) | "
                 f"teleport: {s['teleport']} ({_pct(s['teleport'],s['n']):.2f}%) | "
                 f"brak accuracy: {s['no_acc']} ({_pct(s['no_acc'],s['n']):.2f}%)")
        L.append(f"- reject (accept=False): {s['reject']} ({_pct(s['reject'],s['n']):.2f}%)")
        L.append(f"- mediana accuracy (gdy obecne): {s['acc_p50']}\n")
    L.append("## 3. Rekomendacja\n")
    L.append("- Próg accuracy: dobrać tak, by reject z TEGO tytułu ≤ ~1-2% fixów "
             "(LIVE 150m ≈ 1.2% historycznie). Za niski próg karze legalne fixy w mieście.")
    L.append("- Teleport: trzymać OBA warunki (jump + speed); LIVE (2km,120km/h) "
             "≈ 0.05-0.10% par — bezpieczne. Poluzować TYLKO gdy shadow pokaże false-positive na realnym ruchu.")
    L.append("- FLIP ENABLE_GPS_ACCURACY_TELEPORT_FILTER dopiero po: (a) ≥kilku dniach shadow z realnym "
             "udziałem GPS w pos_source (zależne od decyzji Adriana o adopcji apki), (b) ACK progów, "
             "(c) sprawdzeniu że reject NIE zbiega się z realnymi przypisaniami (cross z PANEL_AGREE).")
    return "\n".join(L)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--md", help="zapisz raport do pliku .md")
    ap.add_argument("--db", default=GPS_DB)
    ap.add_argument("--shadow", default=SHADOW_LOG)
    args = ap.parse_args()
    hist = analyze_history(args.db)
    shadow = analyze_shadow(args.shadow)
    report = render(hist, shadow)
    print(report)
    if args.md:
        os.makedirs(os.path.dirname(os.path.abspath(args.md)), exist_ok=True)
        with open(args.md, "w", encoding="utf-8") as f:
            f.write(report + "\n")
        print(f"\n[zapisano] {args.md}", file=sys.stderr)


if __name__ == "__main__":
    main()
