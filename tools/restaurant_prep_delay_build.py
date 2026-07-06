#!/usr/bin/env python3
"""restaurant_prep_delay_build — T2.1 (advisory Tura 2): mapa realnego prep-delay
per-restauracja z eksportu panelu Rutcom (kolumna WIARYGODNA, nie apka).

Sygnały (per restauracja, z CSV panelu):
 - `oczekiwanie odbiór` (stoper GG:MM:SS) = ile kurier CZEKAŁ pod kuchnią (jedzenie
   nie gotowe gdy dojechał). med + p90. 77% zer → sygnał w ogonie.
 - `pickup_delay` = `czas odbioru` − `czas kuriera` (faktyczny odbiór − committed R27):
   ile PÓŹNIEJ niż obiecano realnie odebrano (łapie i opóźnienie kuchni, i poślizg).
 - `prep_actual` = `czas odbioru` − `czas restauracji` (odbiór − deklarowana gotowość).

shrinkage do globalnej mediany dla rzadkich (n<MIN_N → waga n/(n+K)).
Filtr: status='doręczone' + wyklucz mosty/test (Dr Tusz/Dentomax/Nadajesz.pl/Test).
Dedup po nr zlecenia. Stabilność: korelacja med(1poł)↔med(2poł) restauracji.

TYLKO POMIAR — zero wpięcia w decyzje (to T2.2). Wynik = restaurant_prep_delay.json.

Użycie:
  python3 tools/restaurant_prep_delay_build.py --source data/panel_csv_oczekiwanie_mission.csv --out MAPA.json
"""
from __future__ import annotations

import argparse
import csv
import json
import re
import statistics
import sys
from collections import defaultdict

csv.field_size_limit(10 ** 7)

MIN_N = 15
SHRINK_K = 20
EXCLUDE_SUBSTR = ("dr tusz", "dentomax", "nadajesz", "test", "3giga", "orthdruk",
                  "interpap", "bravilor", "street-sport", "mali wojownicy")
_DATE_RE = re.compile(r"\d{4}-\d{2}-\d{2}")


def _hms(s):
    """GG:MM:SS → minuty."""
    try:
        p = s.strip().split(":")
        if len(p) == 3:
            return int(p[0]) * 60 + int(p[1]) + int(p[2]) / 60.0
    except (ValueError, AttributeError):
        pass
    return None


def _hm(s):
    """HH:MM → minuty od północy."""
    try:
        p = s.strip().split(":")
        if len(p) == 2:
            return int(p[0]) * 60 + int(p[1])
    except (ValueError, AttributeError):
        pass
    return None


def _wrap_diff(a, b):
    """a − b w minutach, korekta przejścia północy (wynik w ±180)."""
    if a is None or b is None:
        return None
    d = a - b
    if d > 720:
        d -= 1440
    elif d < -720:
        d += 1440
    return d if -180 < d < 180 else None


def _excluded(name):
    n = (name or "").lower()
    return any(s in n for s in EXCLUDE_SUBSTR)


def load_records(source):
    with open(source, newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    seen = set()
    out = []
    for r in rows:
        oid = (r.get("nr zlecenia") or "").strip()
        if not oid.isdigit() or oid in seen:
            continue
        if r.get("status") != "doręczone":
            continue
        name = r.get("nazwa restauracji") or ""
        if _excluded(name):
            continue
        if not _DATE_RE.match(r.get("data złożenia zlecenia") or ""):
            continue
        seen.add(oid)
        out.append({
            "oid": oid, "date": r["data złożenia zlecenia"][:10], "name": name,
            "oczek": _hms(r.get("oczekiwanie odbiór", "")),
            "pickup_delay": _wrap_diff(_hm(r.get("czas odbioru")), _hm(r.get("czas kuriera"))),
            "prep_actual": _wrap_diff(_hm(r.get("czas odbioru")), _hm(r.get("czas restauracji"))),
        })
    return out


def _med(x):
    return round(statistics.median(x), 2) if x else None


def _p90(x):
    return round(sorted(x)[int(0.9 * len(x))], 2) if x else None


def build(records):
    byr = defaultdict(lambda: {"oczek": [], "pickup_delay": [], "prep_actual": []})
    for rec in records:
        for k in ("oczek", "pickup_delay", "prep_actual"):
            if rec[k] is not None:
                byr[rec["name"]][k].append(rec[k])
    # globalne mediany (shrinkage target)
    g = {k: _med([v for d in byr.values() for v in d[k]]) or 0.0
         for k in ("oczek", "pickup_delay", "prep_actual")}
    restaurants = {}
    for name, d in byr.items():
        n = len(d["pickup_delay"]) or len(d["oczek"])
        if n < 5:
            continue
        w = n / (n + SHRINK_K)
        entry = {"n": n}
        for k in ("oczek", "pickup_delay", "prep_actual"):
            m = _med(d[k])
            entry[f"{k}_med"] = m
            entry[f"{k}_p90"] = _p90(d[k])
            # shrunk median (do globalnej dla rzadkich)
            entry[f"{k}_shrunk"] = round(w * (m if m is not None else g[k])
                                         + (1 - w) * g[k], 2)
        restaurants[name] = entry
    return {"schema": "restaurant_prep_delay_v1", "min_n": MIN_N, "shrink_k": SHRINK_K,
            "global": g, "n_restaurants": len(restaurants),
            "restaurants": restaurants}


def stability(records):
    """Korelacja med(pickup_delay) 1poł↔2poł okresu po restauracji (n≥MIN_N w obu)."""
    dates = sorted({r["date"] for r in records})
    cut = dates[len(dates) // 2]
    h1 = defaultdict(list)
    h2 = defaultdict(list)
    for r in records:
        if r["pickup_delay"] is None:
            continue
        (h1 if r["date"] <= cut else h2)[r["name"]].append(r["pickup_delay"])
    pairs = [(statistics.median(h1[n]), statistics.median(h2[n]))
             for n in h1 if n in h2 and len(h1[n]) >= MIN_N and len(h2[n]) >= MIN_N]
    if len(pairs) < 3:
        return {"corr": None, "n_pairs": len(pairs), "cut": cut}
    xs = [a for a, _ in pairs]
    ys = [b for _, b in pairs]
    try:
        corr = statistics.correlation(xs, ys)
    except (statistics.StatisticsError, AttributeError):
        corr = None
    return {"corr": round(corr, 3) if corr is not None else None,
            "n_pairs": len(pairs), "cut": cut}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--source", default="/root/ziomek-advisory/data/panel_csv_oczekiwanie_mission.csv")
    ap.add_argument("--out", default="/root/.openclaw/workspace/dispatch_state/restaurant_prep_delay.json")
    ap.add_argument("--report", action="store_true")
    a = ap.parse_args(argv)
    recs = load_records(a.source)
    m = build(recs)
    m["stability"] = stability(recs)
    m["n_records"] = len(recs)
    with open(a.out, "w", encoding="utf-8") as f:
        json.dump(m, f, ensure_ascii=False, indent=1)
    print(f"→ {a.out}  ({len(recs)} rekordów, {m['n_restaurants']} restauracji, "
          f"stabilność corr={m['stability']['corr']} n_par={m['stability']['n_pairs']})")
    if a.report:
        rr = sorted(m["restaurants"].items(),
                    key=lambda kv: kv[1].get("pickup_delay_med") or 0, reverse=True)
        print("\nTOP-15 wolne (pickup_delay_med / oczek_p90 / n):")
        for name, e in rr[:15]:
            print(f"  {name[:34]:34} pd_med={e['pickup_delay_med']:5} oczek_p90={e['oczek_p90']:5} n={e['n']}")
        print("\nNAJSZYBSZE (pickup_delay_med):")
        for name, e in rr[-8:]:
            print(f"  {name[:34]:34} pd_med={e['pickup_delay_med']:5} oczek_p90={e['oczek_p90']:5} n={e['n']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
