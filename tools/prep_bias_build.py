#!/usr/bin/env python3
"""[C1] Agregacja prep-bias kuchni — CZYSTO OFFLINE.

Buduje tabelę biasu czasu przygotowania (realny - deklarowany, ze znakiem)
per restauracja, ale TYLKO z czystego sygnału kuchni:

    "kurier dotarł PRZED gotowością i czekał"  (ready_basis == "waited")

W tym przypadku kurier NIE jest wąskim gardłem — moment odbioru odzwierciedla
realną gotowość kuchni, więc (pickup - declared) mierzy o ile kuchnia zeszła.

WYKLUCZAMY przypadki kontaminowane:

  * ready_basis == "no_arrival_signal" (arrival_source == "commit_fallback")
        Brak realnego sygnału GPS dotarcia. prep_bias = pickup - declared, ale
        pickup mógł być późny BO kurier dojechał późno (kurier = wąskie gardło),
        a nie bo kuchnia była wolna. Nie wiemy ile kuchnia realnie zeszła.

  * ready_basis == "ready_by_arrival"
        Kuchnia była gotowa ZANIM kurier dojechał (kurier nie czekał). Mierzy to
        stronę kuriera, nie wolność kuchni — bias ujemny ograniczony momentem
        dotarcia, nie realnym (wcześniejszym) momentem gotowości.

Dla każdej restauracji (po filtrze czystego sygnału):
  * mediana biasu (min, ze znakiem)
  * p80 biasu (min)
  * EWMA 30 dni (waga maleje z wiekiem rekordu)
  * shrinkage do globalnej mediany dla małej próby (n < próg)

Output JSON: dispatch_state/prep_bias_table.json
  {restaurant_id: {bias_median_min, bias_p80_min, ewma_min, n, shrunk: bool}}
  + sekcja "_global": {bias_median_min, bias_p80_min, n_clean, n_total,
                       n_restaurants, n_excluded_contaminated, shrink_threshold,
                       ewma_halflife_days, generated_at}

Tworzy TYLKO ten plik wyjściowy. Nie dotyka modułów silnika. Fail-soft.
"""

import json
import math
import os
import sys
from datetime import datetime, timezone

# --- ścieżki (absolutne, niezależne od cwd) ---
DISPATCH_STATE = "/root/.openclaw/workspace/dispatch_state"
DEFAULT_LOG = os.path.join(DISPATCH_STATE, "ready_at_log.jsonl")
DEFAULT_OUT = os.path.join(DISPATCH_STATE, "prep_bias_table.json")

# --- parametry agregacji ---
SHRINK_THRESHOLD = 8          # n < THRESHOLD -> waż w stronę globalnej mediany
SHRINK_PRIOR_STRENGTH = 8.0   # "wirtualna" liczba obserwacji priora (globalna mediana)
EWMA_HALFLIFE_DAYS = 30.0     # 30-dniowy okres półtrwania wagi EWMA

# Czysty sygnał kuchni: kurier dotarł i CZEKAŁ na gotowość.
CLEAN_READY_BASIS = "waited"
# Jawnie kontaminowane (dla raportu rozbicia powodów wykluczenia).
CONTAMINATED_BASES = ("no_arrival_signal", "ready_by_arrival")


def _parse_ts(s):
    """ISO -> aware datetime (UTC) albo None. Fail-soft."""
    if not s:
        return None
    try:
        dt = datetime.fromisoformat(s)
    except (ValueError, TypeError):
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def _median(vals):
    if not vals:
        return None
    s = sorted(vals)
    n = len(s)
    mid = n // 2
    if n % 2:
        return float(s[mid])
    return (s[mid - 1] + s[mid]) / 2.0


def _percentile(vals, pct):
    """Percentyl metodą liniowej interpolacji (pct w [0,100])."""
    if not vals:
        return None
    s = sorted(vals)
    n = len(s)
    if n == 1:
        return float(s[0])
    rank = (pct / 100.0) * (n - 1)
    lo = int(math.floor(rank))
    hi = int(math.ceil(rank))
    if lo == hi:
        return float(s[lo])
    frac = rank - lo
    return float(s[lo] + (s[hi] - s[lo]) * frac)


def load_records(log_path):
    """Wczytaj surowe rekordy z JSONL. Fail-soft per linia.

    Zwraca (records, n_total, n_bad_lines).
    """
    records = []
    n_total = 0
    n_bad = 0
    if not os.path.exists(log_path):
        return records, 0, 0
    with open(log_path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            n_total += 1
            try:
                d = json.loads(line)
            except (ValueError, TypeError):
                n_bad += 1
                continue
            if not isinstance(d, dict):
                n_bad += 1
                continue
            records.append(d)
    return records, n_total, n_bad


def is_clean_signal(rec):
    """True jeśli rekord to czysty sygnał kuchni (kurier-czekał).

    Wymaga: ready_basis == "waited" (kurier dotarł, real GPS, czekał na kuchnię)
    ORAZ realnego timestampu dotarcia (status4) ORAZ liczbowego biasu.
    """
    if rec.get("ready_basis") != CLEAN_READY_BASIS:
        return False
    # czysty sygnał MUSI mieć realne dotarcie (status4 / niepusty arrived_at)
    if not rec.get("arrived_at_iso"):
        return False
    if rec.get("arrival_source") == "commit_fallback":
        return False
    bias = rec.get("prep_bias_min")
    if not isinstance(bias, (int, float)):
        return False
    if isinstance(bias, bool):
        return False
    return True


def extract_clean(records):
    """Z surowych rekordów wyciągnij czyste obserwacje per restauracja.

    Zwraca:
      by_rest: {restaurant: [(ts_dt, bias_min), ...]}
      n_clean: int
      excl_reasons: {reason: count}
    """
    by_rest = {}
    n_clean = 0
    excl = {"no_arrival_signal": 0, "ready_by_arrival": 0,
            "missing_arrival_ts": 0, "bad_bias": 0, "other": 0}
    for rec in records:
        if is_clean_signal(rec):
            rest = rec.get("restaurant")
            if not rest:
                excl["other"] += 1
                continue
            bias = float(rec["prep_bias_min"])
            ts = _parse_ts(rec.get("ts")) or _parse_ts(rec.get("picked_up_at_iso"))
            by_rest.setdefault(rest, []).append((ts, bias))
            n_clean += 1
            continue
        # nie-czyste: sklasyfikuj powód wykluczenia (do raportu)
        rb = rec.get("ready_basis")
        if rb in ("no_arrival_signal", "ready_by_arrival"):
            # może też brakować arrival ts mimo "waited"-podobnego stanu, ale tu rb decyduje
            if rb == "no_arrival_signal":
                excl["no_arrival_signal"] += 1
            else:
                excl["ready_by_arrival"] += 1
        elif rb == CLEAN_READY_BASIS and not rec.get("arrived_at_iso"):
            excl["missing_arrival_ts"] += 1
        elif rb == CLEAN_READY_BASIS and not isinstance(rec.get("prep_bias_min"), (int, float)):
            excl["bad_bias"] += 1
        else:
            excl["other"] += 1
    return by_rest, n_clean, excl


def ewma_over_time(obs, now=None, halflife_days=EWMA_HALFLIFE_DAYS):
    """Ważona wykładniczo średnia biasu, waga maleje z wiekiem rekordu.

    obs: lista (ts_dt_or_None, bias). Rekordy bez ts dostają wagę 1.0
    (neutralny fallback). Zwraca float albo None gdy brak obserwacji.
    """
    if not obs:
        return None
    if now is None:
        # najnowszy znany timestamp jako "teraz" (deterministycznie wzgl. danych)
        tss = [t for t, _ in obs if t is not None]
        now = max(tss) if tss else None
    lam = math.log(2.0) / max(halflife_days, 1e-9)
    num = 0.0
    den = 0.0
    for ts, bias in obs:
        if ts is not None and now is not None:
            age_days = (now - ts).total_seconds() / 86400.0
            if age_days < 0:
                age_days = 0.0
            w = math.exp(-lam * age_days)
        else:
            w = 1.0
        num += w * bias
        den += w
    if den <= 0:
        return None
    return num / den


def build_table(by_rest, shrink_threshold=SHRINK_THRESHOLD,
                prior_strength=SHRINK_PRIOR_STRENGTH):
    """Zbuduj tabelę per restauracja z medianą, p80, EWMA i shrinkage.

    Globalna mediana liczona ze WSZYSTKICH czystych obserwacji (pooled),
    by prior był stabilny. Shrinkage tylko dla n < shrink_threshold:
        shrunk_median = (n*rest_median + prior_strength*global_median)
                        / (n + prior_strength)
    """
    # pooled global z wszystkich czystych biasów
    all_biases = [b for obs in by_rest.values() for _, b in obs]
    global_median = _median(all_biases)
    global_p80 = _percentile(all_biases, 80)

    table = {}
    for rest, obs in by_rest.items():
        biases = [b for _, b in obs]
        n = len(biases)
        raw_median = _median(biases)
        raw_p80 = _percentile(biases, 80)
        ewma = ewma_over_time(obs)

        shrunk = n < shrink_threshold
        if shrunk and global_median is not None and raw_median is not None:
            med = (n * raw_median + prior_strength * global_median) / (n + prior_strength)
            p80 = (n * raw_p80 + prior_strength * global_p80) / (n + prior_strength) \
                if (raw_p80 is not None and global_p80 is not None) else raw_p80
            ew = (n * ewma + prior_strength * global_median) / (n + prior_strength) \
                if ewma is not None else global_median
        else:
            med, p80, ew = raw_median, raw_p80, ewma

        table[rest] = {
            "bias_median_min": round(med, 2) if med is not None else None,
            "bias_p80_min": round(p80, 2) if p80 is not None else None,
            "ewma_min": round(ew, 2) if ew is not None else None,
            "n": n,
            "shrunk": bool(shrunk),
        }
    return table, global_median, global_p80, len(all_biases)


def assemble_payload(records, n_total, n_bad):
    """Zbuduj payload (tabela + _global) z już wczytanych rekordów.

    Wyodrębnione, by testy mogły podać rekordy in-memory bez pliku.
    n_total/n_bad podajemy z loadera (albo z len(records) dla in-memory).
    """
    by_rest, n_clean, excl = extract_clean(records)
    table, global_median, global_p80, _n_pooled = build_table(by_rest)

    payload = dict(table)
    payload["_global"] = {
        "bias_median_min": round(global_median, 2) if global_median is not None else None,
        "bias_p80_min": round(global_p80, 2) if global_p80 is not None else None,
        "n_clean": n_clean,
        "n_total": n_total,
        "n_bad_lines": n_bad,
        "n_restaurants": len(table),
        "n_excluded_contaminated": excl["no_arrival_signal"] + excl["ready_by_arrival"],
        "excluded_breakdown": excl,
        "shrink_threshold": SHRINK_THRESHOLD,
        "ewma_halflife_days": EWMA_HALFLIFE_DAYS,
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }
    return payload


def build_from_records(records):
    """Pomocnik testowy: payload bez I/O. n_total = liczba podanych rekordów."""
    return assemble_payload(records, n_total=len(records), n_bad=0)


def build(log_path=DEFAULT_LOG, out_path=DEFAULT_OUT, write=True):
    """Pełny pipeline. Zwraca dict z tabelą + metrykami (do testów/raportu)."""
    records, n_total, n_bad = load_records(log_path)
    payload = assemble_payload(records, n_total, n_bad)
    payload["_global"]["source_log"] = log_path

    if write:
        tmp = out_path + ".tmp"
        try:
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(payload, f, ensure_ascii=False, indent=2, sort_keys=True)
            os.replace(tmp, out_path)
        except OSError as e:
            sys.stderr.write(f"[prep_bias_build] WARN: nie udało się zapisać {out_path}: {e}\n")

    return payload


def _print_report(payload):
    g = payload["_global"]
    print("=== prep_bias_build — raport ===")
    print(f"rekordów total (linie JSONL): {g['n_total']}  (bad: {g['n_bad_lines']})")
    print(f"czystych (kurier-czekał, ready_basis='waited' + real GPS): {g['n_clean']}")
    print(f"wykluczonych kontaminowanych: {g['n_excluded_contaminated']}")
    print(f"  rozbicie wykluczeń: {g['excluded_breakdown']}")
    print(f"restauracji w tabeli: {g['n_restaurants']}")
    print(f"globalna mediana biasu: {g['bias_median_min']} min  | p80: {g['bias_p80_min']} min")
    print(f"shrink_threshold (n<): {g['shrink_threshold']}  | EWMA half-life: {g['ewma_halflife_days']}d")
    print()
    rows = [(r, v) for r, v in payload.items() if r != "_global"]
    # TOP dodatni bias (zaniżają deklarację: realnie wolniejsi niż mówią), n>=threshold dla wiarygodności
    reliable = [(r, v) for r, v in rows if v["n"] >= g["shrink_threshold"]]
    reliable.sort(key=lambda kv: (kv[1]["bias_median_min"] is None, -(kv[1]["bias_median_min"] or -1e9)))
    print(f"TOP restauracje z najwyższym dodatnim biasem (n>={g['shrink_threshold']}, wiarygodne):")
    for r, v in reliable[:8]:
        print(f"  {r:38} median={v['bias_median_min']:>6} p80={v['bias_p80_min']:>6} "
              f"ewma={v['ewma_min']:>6} n={v['n']:>3} shrunk={v['shrunk']}")
    print()
    print("Pełna tabela (sort wg mediany malejąco):")
    allrows = sorted(rows, key=lambda kv: (kv[1]["bias_median_min"] is None,
                                           -(kv[1]["bias_median_min"] or -1e9)))
    for r, v in allrows:
        print(f"  {r:38} median={v['bias_median_min']:>6} p80={v['bias_p80_min']:>6} "
              f"ewma={v['ewma_min']:>6} n={v['n']:>3} shrunk={v['shrunk']}")


def main(argv=None):
    argv = argv if argv is not None else sys.argv[1:]
    log_path = DEFAULT_LOG
    out_path = DEFAULT_OUT
    write = True
    for a in argv:
        if a.startswith("--log="):
            log_path = a.split("=", 1)[1]
        elif a.startswith("--out="):
            out_path = a.split("=", 1)[1]
        elif a == "--dry-run":
            write = False
    payload = build(log_path=log_path, out_path=out_path, write=write)
    _print_report(payload)
    if write:
        print(f"\n[OK] zapisano: {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
