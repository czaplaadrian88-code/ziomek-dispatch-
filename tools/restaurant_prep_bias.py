"""restaurant_prep_bias — tablica prep-bias restauracja×slot (SP-B2-PREPBIAS).

Problem (raport Bartek 2.0 §3.1.5, QW2; mining H3/H19): restauracje
przewidywalnie "spóźnione" względem własnej deklaracji gotowości — mediana
+9-22 min per restauracja×slot. Deklaracja ≠ rzeczywistość → złe ETA, jedzenie
leży (med 14 min), czasówki źle planowane. Std per restauracja = wejście do
SP-B2-SYNCWORKA ("nie łącz dwóch loterii").

Generator (TEN plik, sesja B): z CSV-historii panelu liczy
    bias = czas_odbioru − czas_restauracji  [min]  (real pickup − declared ready)
per restauracja × slot i zapisuje dispatch_state/restaurant_prep_bias.json.
Cron daily 04:15 Warsaw.

FORMAT WYJŚCIA = KONTRAKT SESJI A (eod_drafts/2026-06-11/
MAP_CONTRACT_calib_maps_sesjaA.md; konsument: dispatch_v2/calib_maps.py,
flaga ENABLE_PREP_BIAS_SHADOW, fail-soft):

    {"version": 1, "generated_at": iso,
     "global": {<slot>: {"bias_med","n","std"}, "all": {...}},
     "restaurants": {<nazwa strip().lower()>: {<slot>: {...}, "all": {...}}}}

  - sloty calib_maps.time_slot_warsaw (peak_lunch/high_risk/peak_dinner/off)
    + "all"; slot komórki z godziny DEKLAROWANEJ gotowości (fallback odbiór);
  - komórki n < MIN_N (30) nie są emitowane (konsument spada na global);
  - bias_med dodatni = restauracja później niż deklaruje.

Źródła i pułapki CSV (/root/panel_history_new/*.csv, format eksportu panelu):
  - BOM (utf-8-sig), multiline `uwagi` w cudzysłowach (csv.DictReader),
    czasy HH:MM bez daty → kotwiczenie do `data złożenia` + rollover +1 dzień
    (zdarzenia są PO created; wzorzec bartek2_workdir/prep_corpus.py),
    field_size_limit podniesiony (pola >131kB w starszych dumpach);
  - pliki nachodzą na siebie (miesięczne + przyrostowe) → dedup po nr zlecenia;
  - tylko status "doręczone"; sanity bias ∈ [-60, +180] min.

restaurant_violations.jsonl NIE wchodzi do bias_med (loguje tylko przypadki
z czekaniem kuriera → selekcja zawyżałaby bias); świeże naruszenia są
raportowane jako diagnostyka top-level `violations_recent_14d`.

Użycie:
  python3 -m dispatch_v2.tools.restaurant_prep_bias [--days 120] [--dry-run]
  # cron (CRON_TZ=Europe/Warsaw): 15 4 * * *

Testy: dispatch_v2/tests/test_b2_restaurant_prep_bias.py.
"""
from __future__ import annotations

import argparse
import csv
import glob
import json
import os
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from typing import Optional

try:
    from dispatch_v2.tools._rotated_logs import iter_jsonl_records
    from dispatch_v2.calib_maps import SLOT_ALL, time_slot_warsaw
except ImportError:  # uruchomienie bezpośrednie: python tools/<plik>.py
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(
        os.path.abspath(__file__)))))
    from dispatch_v2.tools._rotated_logs import iter_jsonl_records
    from dispatch_v2.calib_maps import SLOT_ALL, time_slot_warsaw

try:
    from zoneinfo import ZoneInfo
    _WARSAW = ZoneInfo("Europe/Warsaw")
except Exception:  # pragma: no cover
    _WARSAW = timezone.utc

CSV_GLOB = "/root/panel_history_new/*.csv"
VIOLATIONS_LOG = "/root/.openclaw/workspace/dispatch_state/restaurant_violations.jsonl"
OUT_PATH = "/root/.openclaw/workspace/dispatch_state/restaurant_prep_bias.json"

MIN_N = 30
DEFAULT_DAYS = 120          # okno świeżości (marzec zmienił politykę sklejania)
BIAS_SANITY = (-60.0, 180.0)
_EXPECTED_HDR_FIRST = "nr zlecenia"

csv.field_size_limit(10_000_000)  # pułapka: pola uwagi >131kB w starszych dumpach


def _parse_created(s: str) -> Optional[datetime]:
    try:
        return datetime.strptime(s.strip(), "%Y-%m-%d %H:%M:%S")
    except (TypeError, ValueError, AttributeError):
        return None


def _combine_hhmm(created: datetime, hhmm: str) -> Optional[datetime]:
    """HH:MM bez daty → kotwiczenie do daty created + rollover +1d (zdarzenia
    są PO created; jeśli wynik >6h wstecz od created → następny dzień —
    wzorzec prep_corpus.py; łapie czasówki za północ)."""
    if not hhmm:
        return None
    try:
        parts = str(hhmm).strip().split(":")
        hh, mm = int(parts[0]), int(parts[1])
    except (ValueError, IndexError):
        return None
    try:
        cand = created.replace(hour=hh, minute=mm, second=0, microsecond=0)
    except ValueError:
        return None
    if cand < created - timedelta(hours=6):
        cand += timedelta(days=1)
    return cand


def slot_for_dt_warsaw(dt: datetime) -> str:
    """Slot kontraktu dla NAIWNEGO datetime w czasie lokalnym Warsaw (czasy CSV
    są lokalne panelu) — przez time_slot_warsaw dla identycznych granic."""
    return time_slot_warsaw(dt.replace(tzinfo=_WARSAW))


def read_csv_observations(days: int):
    """Czyta wszystkie CSV historii, deduplikuje po zid.

    Zwraca listę (restaurant_norm, slot, bias_min) + statystyki per plik.
    """
    cutoff_date = (datetime.now(timezone.utc).astimezone(_WARSAW)
                   - timedelta(days=days)).replace(tzinfo=None)
    seen_zid: set = set()
    obs = []
    file_stats = {}
    # sortuj malejąco po mtime — najświeższy plik wygrywa dedup po zid
    paths = sorted(glob.glob(CSV_GLOB), key=lambda p: os.path.getmtime(p),
                   reverse=True)
    for path in paths:
        n_rows = n_used = 0
        try:
            with open(path, encoding="utf-8-sig", newline="") as f:
                reader = csv.DictReader(f)
                hdr = reader.fieldnames or []
                if not hdr or (hdr[0] or "").strip().lower() != _EXPECTED_HDR_FIRST:
                    file_stats[os.path.basename(path)] = "SKIP (obcy nagłówek)"
                    continue
                for row in reader:
                    n_rows += 1
                    zid = (row.get("nr zlecenia") or "").strip()
                    if not zid or zid in seen_zid:
                        continue
                    seen_zid.add(zid)
                    if (row.get("status") or "").strip() != "doręczone":
                        continue
                    created = _parse_created(row.get("data złożenia zlecenia") or "")
                    if created is None or created < cutoff_date:
                        continue
                    rest = (row.get("nazwa restauracji") or "").strip().lower()
                    if not rest:
                        continue
                    t_rest = _combine_hhmm(created, row.get("czas restauracji") or "")
                    t_odbior = _combine_hhmm(created, row.get("czas odbioru") or "")
                    if t_rest is None or t_odbior is None:
                        continue
                    bias = (t_odbior - t_rest).total_seconds() / 60.0
                    if not (BIAS_SANITY[0] <= bias <= BIAS_SANITY[1]):
                        continue
                    obs.append((rest, slot_for_dt_warsaw(t_rest), bias))
                    n_used += 1
        except OSError as e:
            file_stats[os.path.basename(path)] = f"SKIP ({e!r})"
            continue
        file_stats[os.path.basename(path)] = f"rows={n_rows} used={n_used}"
    return obs, file_stats


def _median(sorted_vals: list) -> float:
    n = len(sorted_vals)
    mid = n // 2
    if n % 2:
        return float(sorted_vals[mid])
    return (sorted_vals[mid - 1] + sorted_vals[mid]) / 2.0


def _std(vals: list) -> float:
    n = len(vals)
    if n < 2:
        return 0.0
    mean = sum(vals) / n
    return (sum((v - mean) ** 2 for v in vals) / (n - 1)) ** 0.5


def _cell(vals: list) -> dict:
    sv = sorted(vals)
    return {
        "bias_med": round(_median(sv), 1),
        "n": len(sv),
        "std": round(_std(sv), 1),
    }


def build_table(obs) -> dict:
    """{'global': {slot/all: cell}, 'restaurants': {norm: {slot/all: cell}}};
    komórki n < MIN_N nie są emitowane."""
    by_rest: dict = {}
    by_global: dict = {}
    for rest, slot, bias in obs:
        for s in (slot, SLOT_ALL):
            by_rest.setdefault(rest, {}).setdefault(s, []).append(bias)
            by_global.setdefault(s, []).append(bias)

    glob_out = {s: _cell(v) for s, v in by_global.items() if len(v) >= MIN_N}
    rest_out = {}
    for rest, slots in by_rest.items():
        cells = {s: _cell(v) for s, v in slots.items() if len(v) >= MIN_N}
        if cells:
            rest_out[rest] = cells
    return {"global": glob_out, "restaurants": rest_out}


def recent_violations_overlay(days: int = 14) -> dict:
    """Diagnostyka (NIE wchodzi do bias_med): świeże naruszenia z panel_watcher
    (restaurant_violations.jsonl loguje tylko przypadki z czekaniem kuriera —
    próba selekcjonowana, ale dobry sygnał ostrzegawczy per restauracja)."""
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    per_rest: dict = {}
    for r in iter_jsonl_records(VIOLATIONS_LOG, cutoff_dt=cutoff):
        try:
            ts = datetime.fromisoformat(str(r.get("ts")).replace("Z", "+00:00"))
            if ts.tzinfo is None:
                ts = ts.replace(tzinfo=timezone.utc)
        except Exception:
            continue
        if ts < cutoff:
            continue
        rest = str(r.get("restaurant") or "").strip().lower()
        try:
            wait = float(r.get("wait_min"))
        except (TypeError, ValueError):
            continue
        if rest:
            per_rest.setdefault(rest, []).append(wait)
    return {
        rest: {"n": len(w), "wait_med": round(_median(sorted(w)), 1)}
        for rest, w in per_rest.items()
    }


def atomic_write_json(path: str, data: dict) -> None:
    d = os.path.dirname(path)
    fd, tmp = tempfile.mkstemp(dir=d, prefix=".restaurant_prep_bias.")
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(data, f, ensure_ascii=False, indent=1)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def run(days: int = DEFAULT_DAYS, dry_run: bool = False) -> dict:
    obs, file_stats = read_csv_observations(days)
    table = build_table(obs)
    payload = {
        "version": 1,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "global": table["global"],
        "restaurants": table["restaurants"],
        # diagnostyka (konsument ignoruje):
        "window_days": days,
        "n_observations": len(obs),
        "min_n": MIN_N,
        "sources": file_stats,
        "violations_recent_14d": recent_violations_overlay(14),
        "semantics": ("bias_med [min] = mediana(czas_odbioru - czas_restauracji);"
                      " dodatni = później niż deklaracja. Lookup konsumenta:"
                      " restaurants[norm][slot] -> global[slot] -> global[all]."),
    }
    if not dry_run:
        atomic_write_json(OUT_PATH, payload)
    return payload


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    p.add_argument("--days", type=int, default=DEFAULT_DAYS)
    p.add_argument("--dry-run", action="store_true", help="bez zapisu")
    p.add_argument("--top", type=int, default=12, help="ile restauracji w skrócie")
    args = p.parse_args(argv)

    payload = run(days=args.days, dry_run=args.dry_run)
    print(f"obserwacje: {payload['n_observations']} (okno {args.days}d), "
          f"restauracje z komórkami: {len(payload['restaurants'])}"
          f"{' DRY-RUN' if args.dry_run else ''}")
    for f, s in payload["sources"].items():
        print(f"  src {f}: {s}")
    g = payload["global"]
    for s in ("peak_lunch", "high_risk", "peak_dinner", "off", "all"):
        if s in g:
            print(f"  global {s:>11}: bias_med={g[s]['bias_med']:+5.1f} "
                  f"std={g[s]['std']:4.1f} n={g[s]['n']}")
    ranked = sorted(payload["restaurants"].items(),
                    key=lambda kv: -(kv[1].get("all") or {}).get("bias_med", 0))
    print(f"  top {args.top} bias (all):")
    for rest, cells in ranked[: args.top]:
        c = cells.get("all")
        if c:
            print(f"    {rest:32s} bias_med={c['bias_med']:+5.1f} "
                  f"std={c['std']:4.1f} n={c['n']}")
    if not args.dry_run:
        print(f"zapisano: {OUT_PATH}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
