#!/usr/bin/env python3
"""TIER-01 regen — odświeżenie stale percentyli tier-stats z realnych danych.

Kontekst (AUDIT_FIX_PLAN 2026-06-10, TIER-01):
  Percentyle w courier_tiers.json (orders_per_wave_p*, bag_time_p90_min,
  speed.delivery_time_p90_min, bundle.*) pochodziły z V3.19g 6-mc analizy
  zamrożonej 2026-04-20 (/tmp/v319g_courier_tiers_preview.json — plik dawno
  zniknął). Te pola są DEKORACYJNE/obserwacyjne — produkcja czyta z
  courier_tiers.json TYLKO bag.tier (label) + bag.cap_override (per-pora capy).
  Percentyle nie wpływają na scoring/feasibility (zweryfikowane grepem
  konsumentów), ale zasilają analitykę i ludzki widok tierów → warto by były
  świeże i niezatrute (cid=61 Krystian ex-courier 2026-04-23).

Źródło danych (świeże, autonomicznie dostępne):
  Postgres nadajesz_panel (papu-postgres :5433), tabela `delivery`
  (57k wpisów, ciągły ingest do dziś). Łańcuch cid: delivery.courier_id ->
  courier.id -> courier.external_id (== dispatch cid: 123=Bartek O, 61=Krystian).

Semantyka (wierna oryginałowi speed_tier_tracker.py + build_v319h_courier_tiers.py):
  - okno: ostatnie WINDOW_DAYS (domyślnie 60) liczone od MAX(created_at) w DB
  - wave/bundle detekcja: gap > BUNDLE_GAP_MIN (8 min) między picked_up_at
    sąsiednich zleceń tego samego kuriera => nowa fala (jak speed_tier_tracker)
  - orders_per_wave_p50/p90/p99 = percentyle rozmiaru fali (wszystkie fale)
  - bag_time_p90_min = p90 z (delivered_at ostatniego - picked_up_at pierwszego)
    per fala (czas "obsługi worka")
  - speed.delivery_time_p90_min = p90 delivery_min dla SINGLETONÓW (fala=1 order)
  - bundle.bundle_rate_orders = udział zleceń w falach >=2 / wszystkie zlecenia
  - bundle.bundle_waves_rate = udział fal >=2 / wszystkie fale
  - eligibility: >= MIN_ORDERS_FOR_STATS (50, jak _meta.eligibility_min_waves)
    zleceń w oknie; mniej => stats None (label zostaje)

Wyjście:
  1. courier_tier_stats.json — NOWY plik danych (per-cid stats + _meta), format
     analogiczny do bloków bag/speed/bundle w courier_tiers.json.
  2. (opcjonalnie, --patch-tiers) zaktualizowana KOPIA courier_tiers.json
     z odświeżonymi polami percentyli — bag.tier i bag.cap_override NIETKNIĘTE.
     Domyślnie pisze do --out-tiers (sandbox); na produkcję podmienia człowiek.

NIE zmienia logiki w common.py (DWELL/SPEED — kalibracja #179 b16100a evergreen).
NIE zmienia bag.tier ani cap_override. To regen DANYCH percentyli, nie reklasyfikacja.

Użycie:
  python3 eod_drafts/2026-06-13/regen_tier_stats.py --dry-run
  python3 eod_drafts/2026-06-13/regen_tier_stats.py \
      --out eod_drafts/2026-06-13/courier_tier_stats.json \
      --patch-tiers --in-tiers /root/.openclaw/workspace/dispatch_state/courier_tiers.json \
      --out-tiers eod_drafts/2026-06-13/courier_tiers.regen.json
"""
from __future__ import annotations

import argparse
import csv
import json
import math
import os
import subprocess
import sys
import tempfile
from collections import defaultdict
from datetime import datetime, timedelta, timezone

# Brak psycopg2 w venv dispatch — używamy psql (obecny w systemie) do eksportu
# CSV, dalej przetwarzamy stdlib. Hasło przez PGPASSWORD env (nie w argv).
PG_HOST = "127.0.0.1"
PG_PORT = "5433"
PG_DB = "nadajesz_panel"
PG_USER = "nadajesz_panel"
PG_PASSWORD = "97dced35583003e3de8836dc7fc5170e"

WINDOW_DAYS = 60
BUNDLE_GAP_MIN = 8.0          # jak speed_tier_tracker.BUNDLE_GAP_MIN
MIN_ORDERS_FOR_STATS = 50     # jak _meta.eligibility_min_waves_for_stats
DELIVERY_MIN_SANE = (1.0, 120.0)
EXCLUDE_NAMES = {"ANULOWANE"}  # placeholdery w courier table

OUT_DEFAULT = "/root/_auton_wt/tier01/dispatch_v2/eod_drafts/2026-06-13/courier_tier_stats.json"


def percentile(sorted_list, q):
    if not sorted_list:
        return None
    n = len(sorted_list)
    if n == 1:
        return sorted_list[0]
    k = (n - 1) * q
    f = math.floor(k)
    c = math.ceil(k)
    if f == c:
        return sorted_list[int(k)]
    return sorted_list[f] + (sorted_list[c] - sorted_list[f]) * (k - f)


def _psql_csv(query):
    """Uruchamia psql z --csv (przecinek + cudzysłowy) i zwraca surowy tekst.

    UWAGA: NIE używać -A (wymusza separator '|' i psuje --csv). --csv +
    -t (tuples-only, bez nagłówka) wystarcza; csv.reader parsuje wynik.
    """
    env = dict(os.environ, PGPASSWORD=PG_PASSWORD)
    cmd = ["psql", "-h", PG_HOST, "-p", PG_PORT, "-U", PG_USER, "-d", PG_DB,
           "--csv", "-t", "-c", query]
    out = subprocess.check_output(cmd, env=env, text=True)
    return out


def _parse_pg_ts(s):
    """Parsuje timestamp z psql ('2026-06-13 08:17:00+00')."""
    if not s:
        return None
    s = s.strip()
    if not s:
        return None
    try:
        return datetime.fromisoformat(s)
    except ValueError:
        # psql daje 'YYYY-MM-DD HH:MM:SS+00' — dolep minuty offsetu
        try:
            return datetime.strptime(s[:19], "%Y-%m-%d %H:%M:%S").replace(
                tzinfo=timezone.utc)
        except ValueError:
            return None


def fetch_rows():
    """Pobiera doręczone zlecenia z oknem WINDOW_DAYS od najnowszego created_at.

    Zwraca dict cid -> list[(picked_up_at, delivered_at)] posortowane po pickup,
    plus cid -> name. Filtruje wpisy bez picked_up_at/delivered_at i ANULOWANE.
    """
    latest_raw = _psql_csv("SELECT MAX(created_at) FROM delivery").strip()
    latest = _parse_pg_ts(latest_raw)
    if latest is None:
        return {}, {}, None, None
    cutoff = latest - timedelta(days=WINDOW_DAYS)
    cutoff_iso = cutoff.isoformat()

    # to_char z 'T' jako tekst literalny w cudzysłowach (bez backslash — psql
    # bez -shell przekazuje string 1:1; backslash zepsuje format).
    rows_csv = _psql_csv(
        "SELECT c.external_id, c.name, "
        "to_char(d.picked_up_at AT TIME ZONE 'UTC','YYYY-MM-DD\"T\"HH24:MI:SS') pu, "
        "to_char(d.delivered_at AT TIME ZONE 'UTC','YYYY-MM-DD\"T\"HH24:MI:SS') dl "
        "FROM delivery d JOIN courier c ON d.courier_id = c.id "
        "WHERE d.created_at >= '" + cutoff_iso + "' "
        "AND d.picked_up_at IS NOT NULL AND d.delivered_at IS NOT NULL "
        "AND d.delivered_at > d.picked_up_at "
        "AND c.external_id ~ '^[0-9]+$' "
        "ORDER BY c.external_id, d.picked_up_at"
    )
    by_cid = defaultdict(list)
    cid_name = {}
    reader = csv.reader(rows_csv.splitlines())
    for row in reader:
        if len(row) != 4:
            continue
        ext_id, name, pu_s, dl_s = row
        if name and name.strip() in EXCLUDE_NAMES:
            continue
        pu = _parse_pg_ts(pu_s)
        dl = _parse_pg_ts(dl_s)
        if pu is None or dl is None:
            continue
        cid = str(ext_id)
        by_cid[cid].append((pu, dl))
        cid_name.setdefault(cid, name)
    return by_cid, cid_name, cutoff, latest


def compute_courier_stats(events):
    """events: list[(picked_up_at, delivered_at)] posortowane po pickup.

    Zwraca dict stats lub None gdy < MIN_ORDERS_FOR_STATS.
    """
    n_orders = len(events)
    if n_orders < MIN_ORDERS_FOR_STATS:
        return None

    # Wave detection: gap > BUNDLE_GAP_MIN między kolejnymi pickupami => nowa fala
    waves = []
    cur_wave = [events[0]]
    for i in range(1, len(events)):
        gap = (events[i][0] - events[i - 1][0]).total_seconds() / 60.0
        if gap > BUNDLE_GAP_MIN:
            waves.append(cur_wave)
            cur_wave = [events[i]]
        else:
            cur_wave.append(events[i])
    if cur_wave:
        waves.append(cur_wave)

    wave_sizes = sorted(len(w) for w in waves)
    n_waves = len(waves)

    # bag_time per wave = delivered_at ostatniego - picked_up_at pierwszego
    bag_times = sorted(
        (w[-1][1] - w[0][0]).total_seconds() / 60.0 for w in waves
    )

    # singleton delivery_min (fala=1)
    singletons = sorted(
        (w[0][1] - w[0][0]).total_seconds() / 60.0
        for w in waves if len(w) == 1
        if DELIVERY_MIN_SANE[0] <= (w[0][1] - w[0][0]).total_seconds() / 60.0 <= DELIVERY_MIN_SANE[1]
    )

    bundle_orders = sum(len(w) for w in waves if len(w) >= 2)
    bundle_waves = sum(1 for w in waves if len(w) >= 2)

    def _r(x, n=2):
        return round(x, n) if x is not None else None

    p90_singleton = percentile(singletons, 0.9) if len(singletons) >= 30 else None

    return {
        "bag": {
            "orders_per_wave_p50": int(round(percentile(wave_sizes, 0.5))),
            "orders_per_wave_p90": int(round(percentile(wave_sizes, 0.9))),
            "orders_per_wave_p99": int(round(percentile(wave_sizes, 0.99))),
            "max_concurrent_observed": int(wave_sizes[-1]),
            "bag_time_p90_min": _r(percentile(bag_times, 0.9), 1),
        },
        "speed": {
            "delivery_time_p90_min": _r(p90_singleton, 1),
            "n_singletons": len(singletons),
        },
        "bundle": {
            "bundle_rate_orders": _r(bundle_orders / n_orders, 3) if n_orders else None,
            "bundle_waves_rate": _r(bundle_waves / n_waves, 3) if n_waves else None,
        },
        "_n_orders": n_orders,
        "_n_waves": n_waves,
    }


def atomic_write(path, data):
    d = os.path.dirname(path) or "."
    os.makedirs(d, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=d, prefix=".regen_tier_stats.")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2, default=str)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    except Exception:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise


def patch_tiers(in_tiers_path, out_tiers_path, stats, cid_name, exclude_cids):
    """Tworzy KOPIĘ courier_tiers.json z odświeżonymi polami percentyli.

    NIE rusza bag.tier, bag.cap_override, speed.tier_proposed, bundle.tier_proposed,
    inactive/coordinator/tier_label. Nadpisuje TYLKO liczbowe percentyle gdy mamy
    świeże stats dla danego cid. Usuwa wpisy z exclude_cids (cid=61).
    """
    with open(in_tiers_path) as f:
        tiers = json.load(f)

    changed = []
    for cid in exclude_cids:
        if cid in tiers:
            del tiers[cid]
            changed.append(f"removed entry cid={cid}")
        meta_gt = tiers.get("_meta", {}).get("tier_ground_truth_cids", {})
        if cid in meta_gt:
            del meta_gt[cid]
            changed.append(f"removed cid={cid} from _meta.tier_ground_truth_cids")

    for cid, st in stats.items():
        entry = tiers.get(cid)
        if not isinstance(entry, dict):
            continue  # nowy kurier nie w pliku — NIE dodajemy (label = decyzja Adriana)
        bag = entry.setdefault("bag", {})
        # nadpisz TYLKO percentyle, zachowaj tier/cap_override
        for k in ("orders_per_wave_p50", "orders_per_wave_p90",
                  "orders_per_wave_p99", "max_concurrent_observed",
                  "bag_time_p90_min"):
            bag[k] = st["bag"][k]
        sp = entry.setdefault("speed", {})
        sp["delivery_time_p90_min"] = st["speed"]["delivery_time_p90_min"]
        bd = entry.setdefault("bundle", {})
        bd["bundle_rate_orders"] = st["bundle"]["bundle_rate_orders"]
        bd["bundle_waves_rate"] = st["bundle"]["bundle_waves_rate"]

    # adnotacja w _meta
    meta = tiers.setdefault("_meta", {})
    meta["last_tier_stats_regen"] = {
        "at": datetime.now(timezone.utc).isoformat(),
        "source": "postgres nadajesz_panel.delivery (last %dd)" % WINDOW_DAYS,
        "note": ("regen percentyli (orders_per_wave/bag_time/delivery_time/bundle); "
                 "bag.tier i cap_override NIETKNIĘTE; cid=61 usunięty (ex-courier)"),
        "tool": "eod_drafts/2026-06-13/regen_tier_stats.py",
    }
    atomic_write(out_tiers_path, tiers)
    return changed


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--out", default=OUT_DEFAULT, help="courier_tier_stats.json path")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--patch-tiers", action="store_true",
                    help="także zapisz kopię courier_tiers.json z odświeżonymi percentylami")
    ap.add_argument("--in-tiers",
                    default="/root/.openclaw/workspace/dispatch_state/courier_tiers.json")
    ap.add_argument("--out-tiers",
                    default="/root/_auton_wt/tier01/dispatch_v2/eod_drafts/2026-06-13/courier_tiers.regen.json")
    ap.add_argument("--exclude-cids", default="61",
                    help="comma-separated cids do usunięcia (ex-courierzy zatruwający percentyle)")
    args = ap.parse_args()

    exclude_cids = {c.strip() for c in args.exclude_cids.split(",") if c.strip()}

    by_cid, cid_name, cutoff, latest = fetch_rows()

    if not by_cid:
        print("ERROR: brak danych delivery", file=sys.stderr)
        return 2

    stats = {}
    skipped_low = 0
    for cid, events in by_cid.items():
        if cid in exclude_cids:
            continue
        st = compute_courier_stats(events)
        if st is None:
            skipped_low += 1
            continue
        st["name"] = cid_name.get(cid)
        stats[cid] = st

    payload = {
        "_meta": {
            "schema_version": "v1",
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "source": "postgres nadajesz_panel.delivery",
            "window_days": WINDOW_DAYS,
            "window_cutoff": cutoff.isoformat() if cutoff else None,
            "window_latest": latest.isoformat() if latest else None,
            "min_orders_for_stats": MIN_ORDERS_FOR_STATS,
            "bundle_gap_min": BUNDLE_GAP_MIN,
            "excluded_cids": sorted(exclude_cids),
            "couriers_with_stats": len(stats),
            "couriers_skipped_low_n": skipped_low,
            "tool": "eod_drafts/2026-06-13/regen_tier_stats.py",
        },
        "couriers": stats,
    }

    # raport
    print(f"=== TIER-01 regen tier-stats ===")
    print(f"window: {WINDOW_DAYS}d  cutoff={cutoff}  latest={latest}")
    print(f"couriers with stats (>= {MIN_ORDERS_FOR_STATS} orders): {len(stats)}")
    print(f"skipped low-n: {skipped_low}   excluded: {sorted(exclude_cids)}")
    print(f"\n{'cid':>5} {'name':<16} {'n_ord':>6} {'opw_p50':>7} {'opw_p90':>7} "
          f"{'bagt_p90':>8} {'deliv_p90':>9} {'bndl_ord':>8}")
    for cid, st in sorted(stats.items(), key=lambda kv: -kv[1]["_n_orders"]):
        b = st["bag"]; s = st["speed"]; bd = st["bundle"]
        print(f"{cid:>5} {str(st['name'])[:16]:<16} {st['_n_orders']:>6} "
              f"{b['orders_per_wave_p50']:>7} {b['orders_per_wave_p90']:>7} "
              f"{str(b['bag_time_p90_min']):>8} {str(s['delivery_time_p90_min']):>9} "
              f"{str(bd['bundle_rate_orders']):>8}")

    if args.dry_run:
        print("\n[DRY-RUN] brak zapisu")
        return 0

    atomic_write(args.out, payload)
    print(f"\nzapisano: {args.out}")

    if args.patch_tiers:
        changed = patch_tiers(args.in_tiers, args.out_tiers, stats, cid_name, exclude_cids)
        print(f"patch courier_tiers -> {args.out_tiers}")
        for c in changed:
            print(f"  {c}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
