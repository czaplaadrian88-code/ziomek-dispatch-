#!/usr/bin/env python3
"""suwak_autonomii_review — dobowy czytelnik SUWAKA AUTONOMII (READ-ONLY, szereg czasowy).

Powód istnienia (owner, 19.07): suwak autonomii to DWIE uczciwe liczby, nie opinia:

  LICZBA 1 — ile Ziomek zrobiłby SAM: odsetek decyzji, które przeszłyby bramkę auto-assign
      w wariancie D. Źródło: `scripts/logs/shadow_decisions.jsonl` (+ rotacje .1/.N/.N.gz),
      pola `would_auto_assign` (bazowy), `would_auto_assign_d`, `would_auto_assign_dprime`,
      kanał `auto_route == "AUTO"`.

  LICZBA 2 — ile niezgody Ziomek↔człowiek to REDYSTRYBUCJA niedoboru floty, a ile realna
      różnica wyboru. Źródło: `dispatch_state/outcomes_clean_shadow.jsonl`, pola `agree`
      (`best_courier_id == real_courier_id`, writer `shadow_collectors.py`) i `pool_feasible`.
      Rozbicie: pool>=3 (człowiek MIAŁ z czego wybierać) vs pool<=2 (wynik wymuszony flotą).

Metodologia jest PRZENIESIONA 1:1 z jednorazowego composera
`/root/artifacts/suwak-composer-20260804/suwak_2_liczby.py` (04.08) — te same mianowniki,
te same wykluczenia, ta sama definicja kubełków puli i ta sama dekompozycja nadwyżki
niezgód. Różnice implementacyjne (NIE metodologiczne) opisane w RAPORCIE sesji 297:
  * wejścia czytane STRUMIENIOWO z projekcją pól — rekord decyzji ma do ~0,7 MB, a job
    dobowy chodzi pod `MemoryMax=1G`; composer trzymał całe rekordy w RAM;
  * rotacje wykrywane globem, nie listą trzech ścieżek — 05.08 `.2.gz` już nie istniało,
    hardcode dawał cichy brak dnia w oknie;
  * wynik = APPEND do szeregu czasowego + snapshot dnia, zamiast nadpisywanego raportu.

JEDNA ŚWIADOMA RÓŻNICA METODOLOGICZNA vs 04.08 (decyzja CTO, sesja 297, do zgłoszenia
ownerowi): indeks joinu bierze decyzję NAJWCZEŚNIEJSZĄ W CZASIE (`ts`), a nie pierwszą
napotkaną. Composer składał pliki od najnowszego i brał pierwszy trafiony rekord, więc dla
zamówienia rozpiętego na dwie rotacje wybierał decyzję PÓŹNIEJSZĄ — sprzecznie z własną
deklaracją. Kierunek: poprawność (zdanie w raporcie ownera ma być prawdziwe). Różnica
ujawnia się WYŁĄCZNIE na zamówieniach z decyzjami po obu stronach granicy rotacji.

Kontrakt (identyczny z resztą czytelników `shadow_review_daily`):
  * 100 % READ-ONLY wobec źródeł: `dispatch_state/`, `scripts/logs/` i `flags.json` są
    WYŁĄCZNIE czytane. Zapis idzie tylko do `--out-dir` / `--series-path` (pod systemd
    jedyna ścieżka zapisu to `ReadWritePaths=/root/artifacts/shadow-review`).
  * ZERO Telegrama — moduł nie zna kanału alertowego; `--no-telegram` przyjmowany dla
    jednolitości wywołania i jako jawna deklaracja w argv (ratchet w testach harnessu).
  * APPEND-ONLY na szeregu: plik szeregu otwierany wyłącznie w trybie "a", nigdy "w";
    zero kasowania, zero truncate. Historia suwaka jest niezniszczalna z tej ścieżki.
  * FAIL-SOFT wobec biegu zbiorczego: wyjątek albo brak/martwy korpus NIE wywala
    `shadow-review` — powstaje rekord z `null` i `reason`, exit 0 i linia WARNING.
    Liczba NIGDY nie powstaje z martwego strumienia (to fail-closed per-liczba).

Uruchomienie ręczne (read-only, wyjście poza żywy stan):
    /root/.openclaw/venvs/dispatch/bin/python -m dispatch_v2.tools.suwak_autonomii_review \
        --no-telegram --out-dir /sciezka/do/raportu/2026-08-05
"""
from __future__ import annotations

import argparse
import glob
import gzip
import hashlib
import json
import os
import re
import sys
import time
from collections import Counter, defaultdict
from datetime import datetime, timezone

SCRIPTS_LOGS = "/root/.openclaw/workspace/scripts/logs"
STATE = "/root/.openclaw/workspace/dispatch_state"
DECISIONS_BASE_DEFAULT = f"{SCRIPTS_LOGS}/shadow_decisions.jsonl"
OUTCOMES_DEFAULT = f"{STATE}/outcomes_clean_shadow.jsonl"
DEFAULT_OUT_ROOT = "/root/artifacts/shadow-review"
SERIES_FILENAME = "suwak_autonomii.jsonl"
SNAPSHOT_JSON = "suwak_autonomii.json"
SNAPSHOT_MD = "SUWAK_AUTONOMII.md"
SERIES_SCHEMA = "suwak_autonomii.series.v1"
SNAPSHOT_SCHEMA = "suwak_autonomii.2_liczby.v1"

# Ten sam próg świeżości co w harnessie (shadow_review_daily): pisarz decyzji chodzi
# co 1-5 min, kolektor outcomes co dobę o 04:40 — 48 h łapie strumień MARTWY, nie przerwę.
DEFAULT_MAX_CORPUS_AGE_H = 48.0

# Rekordy lifecycle/obserwacyjne — NIE są decyzjami dyspozytorskimi, wypadają z mianownika.
NON_DECISION_RECORD_TYPES = {"CZASOWKA_RECLAIM_EVALUATION"}
NON_DECISION_KINDS = {"lifecycle_observation"}

# Projekcja: z rekordu decyzji (do ~0,7 MB) zostaje TYLKO to, czego wymaga metodologia.
# Obecność klucza jest znacząca (mianowniki liczone po "k in r"), więc projekcja kopiuje
# klucze warunkowo — brak pola w źródle = brak pola w projekcji.
DECISION_FIELDS = (
    "ts", "event_id", "order_id", "verdict", "record_type", "decision_kind",
    "would_auto_assign", "would_auto_assign_d", "would_auto_assign_dprime",
    "auto_route", "pool_feasible_count", "auto_block_reasons_d",
)
OUTCOME_FIELDS = ("oid", "day", "agree", "pool_feasible")

GATE_FIELDS = {
    "would_auto_assign": "bazowy (dzisiejsze bramki)",
    "would_auto_assign_d": "wariant D",
    "would_auto_assign_dprime": "wariant D'",
}

# Rotacje logrotate: shadow_decisions.jsonl.1, .2, .2.gz ... (delaycompress).
# WHITELIST (kanoniczny kształt rotacji), NIE blacklista po końcówce ścieżki: katalog logów
# jest współdzielony z plikami, które korpusem NIE są (`<baza>.append.lock` powstaje w
# core/jsonl_rotation.py, kopie operatorskie `<baza>.bak`), a każdy z nich może zostać
# obrócony do `<baza>.<cokolwiek>.<N>`. Dopasowanie na SAMEJ NAZWIE pliku, nie na końcu
# ścieżki — inaczej taki plik cicho wchodzi do MIANOWNIKA obu liczb.
_ROTATION_SUFFIX_RE = r"\.(\d+)(\.gz)?"


# ───────────────────────── narzędzia ─────────────────────────

def pct(n, d):
    return (100.0 * n / d) if d else None


def fmt_pct(x, nd=2):
    return "n/d" if x is None else f"{x:.{nd}f}%"


def _now():
    return datetime.now(timezone.utc)


def _iso(dt):
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def warn(msg):
    """Jedna konwencja ostrzeżeń — linia trafia do `<czytelnik>.log` harnessu."""
    print(f"WARNING suwak_autonomii: {msg}", file=sys.stderr)


def open_text(path):
    if path.endswith(".gz"):
        return gzip.open(path, "rt", encoding="utf-8", errors="replace")
    return open(path, "r", encoding="utf-8", errors="replace")


def sha256_and_size(path):
    h = hashlib.sha256()
    size = 0
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(chunk)
            size += len(chunk)
    return h.hexdigest(), size


def project(rec, fields):
    """Mała kopia rekordu — obecność klucza zachowana, wartości nie tknięte."""
    return {k: rec[k] for k in fields if k in rec}


def iter_projected(path, fields):
    """Strumieniowo czyta JSONL i zwraca (projekcja, liczba złych linii).

    Plik otwierany i zamykany jednorazowo (bez trzymania fd — logi podlegają rotacji).
    Cały rekord żyje tylko na czas projekcji, więc pamięć nie rośnie z rozmiarem linii.
    """
    bad = 0
    out = []
    with open_text(path) as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except Exception:
                bad += 1
                continue
            if isinstance(rec, dict):
                out.append(project(rec, fields))
            else:
                bad += 1
    return out, bad


def pool_bucket(v):
    """'>=3' | '<=2' | 'unknown' — jedna definicja dla obu źródeł."""
    if not isinstance(v, (int, float)) or isinstance(v, bool):
        return "unknown"
    return ">=3" if v >= 3 else "<=2"


def _ts_order_key(rec):
    """Klucz chronologiczny rekordu decyzji; brak/nie-tekstowy `ts` ląduje na końcu."""
    ts = rec.get("ts")
    ts = ts if isinstance(ts, str) else ""
    return (ts == "", ts)


def day_range(days):
    d = sorted(x for x in days if x)
    return (d[0], d[-1], len(set(d))) if d else (None, None, 0)


def corpus_state(path, max_age_h):
    """Stan korpusu: (żywy?, powód, meta). Martwy/pusty/brak = liczba NIE powstaje."""
    if not os.path.exists(path):
        return False, f"korpus nie istnieje: {path}", {"path": path, "present": False}
    st = os.stat(path)
    age_h = (time.time() - st.st_mtime) / 3600.0
    meta = {
        "path": path,
        "present": True,
        "size_bytes": st.st_size,
        "mtime_utc": _iso(datetime.fromtimestamp(st.st_mtime, timezone.utc)),
        "age_hours": round(age_h, 1),
    }
    if st.st_size == 0:
        return False, f"korpus PUSTY (0 B): {path}", meta
    if age_h > max_age_h:
        return False, (f"korpus MARTWY — ostatni zapis {round(age_h / 24.0, 1)} dni temu "
                       f"(próg {max_age_h} h): {path}"), meta
    return True, "", meta


def rotation_index(base, path):
    """Numer rotacji `path` względem `base`, albo None gdy to NIE jest rotacja korpusu.

    Korpusem jest wyłącznie `<baza>.<N>` i `<baza>.<N>.gz` — nic więcej. `<baza>.bak.1`,
    `<baza>.append.lock.1`, `<baza>.old.7`, `<baza>.tmp` to pliki obce i mają zwrócić None.
    """
    m = re.fullmatch(re.escape(os.path.basename(base)) + _ROTATION_SUFFIX_RE,
                     os.path.basename(path))
    return int(m.group(1)) if m else None


def discover_decision_files(base):
    """Bazowy log + rotacje (.1, .2, .2.gz ...), od najnowszej do najstarszej.

    Composer miał trzy ścieżki na sztywno; 05.08 `.2.gz` już nie istniało (logrotate
    `daily/rotate 30/size 100M`), więc hardcode po cichu zawężał okno. Kanonem jest
    to, co logrotate faktycznie zostawił na dysku — ale TYLKO w kanonicznym kształcie
    rotacji (patrz `rotation_index`).
    """
    files = []
    if os.path.exists(base):
        files.append((0, base))
    for path in glob.glob(base + ".*"):
        n = rotation_index(base, path)
        if n is None:
            continue  # .append.lock(.1), .bak(.1), .tmp itp. — nie są korpusem
        files.append((n, path))
    files.sort(key=lambda x: x[0])
    return [p for _, p in files]


# ───────────────── LICZBA 1: gotowość auto-assign ─────────────────

def compute_liczba1(base, max_age_h):
    """Zwraca (wynik, manifest, indeks order_id→decyzja). Wynik ma `reason` gdy null.

    `base` to KANONICZNA ścieżka żywego logu i jedyne wejście — rotacje dobierane są tutaj,
    więc wołający nie może sparować bazy z cudzą listą plików. Tożsamość pliku żywego bierze
    się z porównania ze ścieżką bazową, NIGDY z pozycji w liście: gdy bazy nie ma, najnowszym
    plikiem jest ROTACJA i liczba powstałaby ze strumienia, do którego nikt już nie pisze.
    """
    paths = discover_decision_files(base)
    base_key = os.path.realpath(base)
    manifest, recs, bad_total = [], [], 0
    live_seen = False
    reasons = []
    for path in paths:
        alive, why, meta = corpus_state(path, max_age_h)
        # Rotacje SĄ z definicji stare — próg świeżości dotyczy pliku BAZOWEGO (żywego).
        is_base = os.path.realpath(path) == base_key
        if is_base and not alive:
            reasons.append(why)
            meta["skipped"] = why
            manifest.append(meta)
            continue
        if not meta.get("present"):
            manifest.append(meta)
            continue
        digest, hashed = sha256_and_size(path)
        rows, bad = iter_projected(path, DECISION_FIELDS)
        bad_total += bad
        lo, hi, nd = day_range([(r.get("ts") or "")[:10] for r in rows])
        meta.update({"sha256": digest, "hashed_bytes": hashed, "records": len(rows),
                     "bad_lines": bad, "day_first": lo, "day_last": hi, "days_distinct": nd})
        manifest.append(meta)
        recs.extend(rows)
        if is_base:
            live_seen = True

    if not live_seen:
        # Same rotacje bez żywego pliku bazowego = strumień zatrzymany. Liczba z takiego
        # korpusu opisywałaby przeszłość udając dzień dzisiejszy → nie powstaje.
        # Sprawdzane PRZED pustką korpusu, bo brak żywej bazy jest przyczyną, nie skutkiem.
        reason = "; ".join(reasons) or f"brak żywego korpusu bazowego: {base}"
        return {"available": False, "reason": reason}, manifest, {}
    if not recs:
        reason = "; ".join(reasons) or "brak rekordów decyzji w dostępnych plikach"
        return {"available": False, "reason": reason}, manifest, {}

    # dedupe po event_id (rotacja nie powinna duplikować, ale sprawdzamy jawnie)
    seen, uniq, dups = set(), [], 0
    for r in recs:
        key = r.get("event_id") or (r.get("ts"), r.get("order_id"), r.get("verdict"))
        if key in seen:
            dups += 1
            continue
        seen.add(key)
        uniq.append(r)

    # mianownik: decyzje dyspozytorskie z polami bramki auto
    decisions, excluded_lifecycle, missing_fields = [], 0, 0
    for r in uniq:
        if r.get("record_type") in NON_DECISION_RECORD_TYPES or r.get("decision_kind") in NON_DECISION_KINDS:
            excluded_lifecycle += 1
            continue
        if "would_auto_assign_d" not in r and "auto_route" not in r:
            missing_fields += 1
            continue
        decisions.append(r)

    coverage = {k: sum(1 for r in decisions if k in r) for k in GATE_FIELDS}
    coverage["auto_route"] = sum(1 for r in decisions if "auto_route" in r)
    # JEDNA definicja pokrycia puli — ta sama, której używają kubełki (`pool_bucket`).
    # `isinstance(True, int)` jest prawdą, więc test na typ liczbowy zaliczał do pokrycia
    # rekordy z boolem, które w segmentacji siedzą w kubełku "unknown".
    coverage["pool_feasible_count"] = sum(
        1 for r in decisions if pool_bucket(r.get("pool_feasible_count")) != "unknown"
    )

    def bucket_stats(rows):
        s = {"n": len(rows)}
        for k in GATE_FIELDS:
            have = [r for r in rows if k in r]
            true_n = sum(1 for r in have if r.get(k) is True)
            s[k] = {"n": len(have), "true": true_n, "pct": pct(true_n, len(have))}
        have_ar = [r for r in rows if "auto_route" in r]
        auto_n = sum(1 for r in have_ar if r.get("auto_route") == "AUTO")
        s["auto_route_AUTO"] = {"n": len(have_ar), "true": auto_n, "pct": pct(auto_n, len(have_ar))}
        s["auto_route_dist"] = dict(Counter(r.get("auto_route") for r in have_ar).most_common())
        return s

    buckets = defaultdict(list)
    for r in decisions:
        buckets[pool_bucket(r.get("pool_feasible_count"))].append(r)

    result = {
        "available": True,
        "reason": None,
        "global": bucket_stats(decisions),
        "pool_ge3": bucket_stats(buckets[">=3"]),
        "pool_le2": bucket_stats(buckets["<=2"]),
        "pool_unknown": bucket_stats(buckets["unknown"]),
    }

    # co blokuje resztę (wariant D) — rodziny powodów
    blockers_all, blockers_first = Counter(), Counter()
    blocked = [r for r in decisions if r.get("would_auto_assign_d") is not True]
    for r in blocked:
        rs = r.get("auto_block_reasons_d")
        if isinstance(rs, list) and rs:
            fam = [str(x).split(":", 1)[0] for x in rs]
            blockers_all.update(set(fam))
            blockers_first[fam[0]] += 1
    result["blockers_d"] = {
        "n_blocked": len(blocked),
        "n_with_reasons": sum(1 for r in blocked
                              if isinstance(r.get("auto_block_reasons_d"), list) and r.get("auto_block_reasons_d")),
        "families_any": dict(blockers_all.most_common(15)),
        "families_first": dict(blockers_first.most_common(15)),
    }

    lo, hi, nd = day_range([(r.get("ts") or "")[:10] for r in decisions])
    result["window"] = {"day_first": lo, "day_last": hi, "days_distinct": nd}
    result["intake"] = {
        "records_read": len(recs),
        "duplicates_dropped": dups,
        "excluded_lifecycle": excluded_lifecycle,
        "excluded_missing_gate_fields": missing_fields,
        "decisions": len(decisions),
        "bad_lines": bad_total,
        "files_read": len([m for m in manifest if m.get("records") is not None]),
    }
    result["coverage"] = coverage

    # indeks order_id → decyzja, do joinu z outcomes. Zamówienie może mieć kilka decyzji
    # (NEW_ORDER + przekierowania) — bierzemy PIERWSZĄ W CZASIE (pierwotną decyzję
    # dyspozytorską), bo to ona odpowiada pytaniu "czy Ziomek zrobiłby to sam".
    # Kolejność WCZYTANIA jest od najnowszego pliku, więc "pierwszy napotkany" byłby dla
    # zamówienia rozpiętego na dwie rotacje decyzją PÓŹNIEJSZĄ — dlatego sortujemy po `ts`.
    # Rekordy bez `ts` idą na koniec: nie mogą wygrać z rekordem o znanym czasie.
    idx = {}
    per_order = Counter()
    for r in sorted(decisions, key=_ts_order_key):
        oid = r.get("order_id")
        if oid is None:
            continue
        oid = str(oid).strip()
        if not oid:
            continue
        per_order[oid] += 1
        if oid not in idx:
            idx[oid] = {
                "would_auto_assign_d": r.get("would_auto_assign_d"),
                "auto_route": r.get("auto_route"),
                "pool_feasible_count": r.get("pool_feasible_count"),
            }
    result["orders"] = {
        "distinct": len(idx),
        "with_multiple_decisions": sum(1 for v in per_order.values() if v > 1),
        "max_decisions_per_order": max(per_order.values()) if per_order else 0,
    }
    return result, manifest, idx


# ───────── LICZBA 2: redystrybucja vs realny błąd (zgodność A1) ─────────

def _agree_stats(subset):
    have = [r for r in subset if isinstance(r.get("agree"), bool)]
    agree_n = sum(1 for r in have if r["agree"] is True)
    return {
        "n_records": len(subset),
        "n_agree_known": len(have),
        "agree": agree_n,
        "disagree": len(have) - agree_n,
        "agree_pct": pct(agree_n, len(have)),
        "disagree_pct": pct(len(have) - agree_n, len(have)),
    }


def _analyze_agreement(subset, label):
    buckets = defaultdict(list)
    for r in subset:
        buckets[pool_bucket(r.get("pool_feasible"))].append(r)
    g = _agree_stats(subset)
    ge3, le2, unk = (_agree_stats(buckets[">=3"]), _agree_stats(buckets["<=2"]),
                     _agree_stats(buckets["unknown"]))

    # ROZKŁAD PRZYCZYNOWY niezgody w segmencie pool<=2:
    # poziom bazowy = stopa niezgody przy pool>=3 (tam człowiek MIAŁ z czego wybierać,
    # więc ta stopa reprezentuje "różnicę zdań / realny błąd" niezależną od floty).
    # Nadwyżka ponad ten poziom = niezgoda WYMUSZONA niedoborem floty (redystrybucja).
    base_rate = ge3["disagree_pct"]
    scarcity = None
    if base_rate is not None and le2["n_agree_known"] and le2["disagree_pct"] is not None:
        expected = le2["n_agree_known"] * base_rate / 100.0
        excess = le2["disagree"] - expected
        scarcity = {
            "baseline_disagree_rate_pct": base_rate,
            "le2_disagree_rate_pct": le2["disagree_pct"],
            "le2_disagree_observed": le2["disagree"],
            "le2_disagree_expected_at_baseline": round(expected, 1),
            "le2_disagree_excess_scarcity": round(excess, 1),
            "excess_share_of_le2_disagreements_pct": pct(excess, le2["disagree"]),
            "excess_share_of_measured_disagreements_pct": pct(excess, le2["disagree"] + ge3["disagree"]),
            "measured_disagreements": le2["disagree"] + ge3["disagree"],
        }
    return {
        "scarcity_decomposition": scarcity,
        "label": label,
        "global": g,
        "pool_ge3": ge3,
        "pool_le2": le2,
        "pool_unknown": unk,
        "pool_coverage_pct": pct(ge3["n_records"] + le2["n_records"], len(subset)),
        "disagreement_split": {
            "total": g["disagree"],
            "from_pool_le2": le2["disagree"],
            "from_pool_le2_pct": pct(le2["disagree"], g["disagree"]),
            "from_pool_ge3": ge3["disagree"],
            "from_pool_ge3_pct": pct(ge3["disagree"], g["disagree"]),
            "from_pool_unknown": unk["disagree"],
            "from_pool_unknown_pct": pct(unk["disagree"], g["disagree"]),
        },
    }


def compute_liczba2(path, max_age_h, window=None):
    """Zwraca (wynik, meta_manifestu, wiersze). Wiersze potrzebne do joinu."""
    alive, why, meta = corpus_state(path, max_age_h)
    if not alive:
        meta["skipped"] = why
        return {"available": False, "reason": why}, meta, []

    digest, hashed = sha256_and_size(path)
    rows, bad = iter_projected(path, OUTCOME_FIELDS)
    lo, hi, nd = day_range([r.get("day") for r in rows])
    meta.update({"sha256": digest, "hashed_bytes": hashed, "records": len(rows),
                 "bad_lines": bad, "day_first": lo, "day_last": hi, "days_distinct": nd})
    if not rows:
        return {"available": False, "reason": f"korpus bez rekordów: {path}"}, meta, []

    res = {"available": True, "reason": None,
           "full": _analyze_agreement(rows, "pełne okno outcomes")}
    if window and window[0] and window[1]:
        lo_w, hi_w = window
        sub = [r for r in rows if r.get("day") and lo_w <= r["day"] <= hi_w]
        res["overlap"] = _analyze_agreement(sub, f"okno wspólne z shadow_decisions {lo_w}..{hi_w}")

    # trend tygodniowy (zgodność + udział pool<=2)
    byweek = defaultdict(list)
    for r in rows:
        d = r.get("day")
        if not d:
            continue
        try:
            iso = datetime.strptime(d, "%Y-%m-%d").isocalendar()
        except Exception:
            continue
        byweek[f"{iso[0]}-W{iso[1]:02d}"].append(r)
    trend = []
    for wk in sorted(byweek):
        sub = byweek[wk]
        s = _agree_stats(sub)
        known_pool = [r for r in sub if pool_bucket(r.get("pool_feasible")) != "unknown"]
        le2 = [r for r in known_pool if pool_bucket(r.get("pool_feasible")) == "<=2"]
        trend.append({"week": wk, "n": s["n_agree_known"], "agree_pct": s["agree_pct"],
                      "pool_known": len(known_pool), "pool_le2_share_pct": pct(len(le2), len(known_pool))})
    res["weekly_trend"] = trend
    res["window"] = {"day_first": lo, "day_last": hi, "days_distinct": nd}
    # KLUCZOWE dla uczciwości: `pool_feasible` jest wypełnione tylko tam, gdzie istnieje join
    # z backfillem — a ten sięga płycej niż cały korpus. Rozbicie pool to WĘŻSZE okno.
    pk_days = [r.get("day") for r in rows if pool_bucket(r.get("pool_feasible")) != "unknown"]
    pk_lo, pk_hi, pk_nd = day_range(pk_days)
    res["pool_known_window"] = {
        "day_first": pk_lo, "day_last": pk_hi, "days_distinct": pk_nd,
        "records": len(pk_days), "share_of_corpus_pct": pct(len(pk_days), len(rows)),
    }
    return res, meta, rows


# ───────── JOIN: te same zamówienia — gotowość auto × zgodność ─────────

def compute_join(outcome_rows, dec_idx, window):
    if not (window and window[0] and window[1]) or not dec_idx or not outcome_rows:
        return {"available": False, "reason": "brak wspólnego okna albo pustego indeksu decyzji"}
    lo_w, hi_w = window
    joined = []
    for r in outcome_rows:
        d = r.get("day")
        if not (d and lo_w <= d <= hi_w):
            continue
        oid = str(r.get("oid") or "").strip()
        if not oid or oid not in dec_idx:
            continue
        if not isinstance(r.get("agree"), bool):
            continue
        joined.append((r, dec_idx[oid]))

    cells = Counter()
    for r, dec in joined:
        cells[(dec.get("would_auto_assign_d") is True, r["agree"])] += 1
    n = len(joined)
    auto_ready = sum(v for (a, _), v in cells.items() if a)
    return {
        "available": True,
        "reason": None,
        "n_joined": n,
        "candidates_outcomes_in_window": sum(
            1 for r in outcome_rows if r.get("day") and lo_w <= r["day"] <= hi_w),
        "decisions_indexed": len(dec_idx),
        "auto_ready_d": auto_ready,
        "auto_ready_d_pct": pct(auto_ready, n),
        "auto_and_agree": cells[(True, True)],
        "auto_and_agree_pct_of_all": pct(cells[(True, True)], n),
        "agree_given_auto_ready_pct": pct(cells[(True, True)], auto_ready),
        "agree_given_not_auto_ready_pct": pct(cells[(False, True)], n - auto_ready),
        "matrix": {
            "auto=T,agree=T": cells[(True, True)],
            "auto=T,agree=F": cells[(True, False)],
            "auto=F,agree=T": cells[(False, True)],
            "auto=F,agree=F": cells[(False, False)],
        },
    }


# ───────────────────────── szereg czasowy + snapshot ─────────────────────────

def build_series_record(day, started, finished, l1, l2, join, manifest, snapshot_path):
    """Jedna linia szeregu: dwie liczby + liczności + okna + tożsamość wejść.

    Świadomie WĄSKI zestaw pól — pełen detal (blokery, trend, macierz joinu) siedzi
    w snapshotcie dnia. Szereg ma się dać czytać jako tabela, nie jako archiwum.
    """
    rec = {
        "schema": SERIES_SCHEMA,
        "day": day,
        "generated_utc": finished,
        "read_started_utc": started,
        "mode": "READ_ONLY",
        "status": "OK",
        "degraded": [],
        "snapshot": snapshot_path,
        "inputs": [{k: m.get(k) for k in ("path", "present", "size_bytes", "mtime_utc",
                                          "age_hours", "records", "bad_lines", "sha256",
                                          "day_first", "day_last", "skipped")}
                   for m in manifest],
    }

    if l1.get("available"):
        g = l1["global"]
        rec["liczba1"] = {
            "metric": "would_auto_assign_d",
            "pct": g["would_auto_assign_d"]["pct"],
            "true": g["would_auto_assign_d"]["true"],
            "n": g["would_auto_assign_d"]["n"],
            "baseline_pct": g["would_auto_assign"]["pct"],
            "dprime_pct": g["would_auto_assign_dprime"]["pct"],
            "auto_route_auto_pct": g["auto_route_AUTO"]["pct"],
            "pool_ge3": {"n": l1["pool_ge3"]["n"], "d_pct": l1["pool_ge3"]["would_auto_assign_d"]["pct"]},
            "pool_le2": {"n": l1["pool_le2"]["n"], "d_pct": l1["pool_le2"]["would_auto_assign_d"]["pct"]},
            "window": l1["window"],
            "intake": l1["intake"],
            "top_blockers": dict(list(l1["blockers_d"]["families_any"].items())[:5]),
            "reason": None,
        }
    else:
        rec["liczba1"] = {"metric": "would_auto_assign_d", "pct": None, "n": None,
                          "reason": l1.get("reason")}
        rec["status"] = "DEGRADED"
        rec["degraded"].append(f"liczba1: {l1.get('reason')}")

    if l2.get("available"):
        full = l2["full"]
        rec["liczba2"] = {
            "metric": "agree_top1_by_pool",
            "global": {"agree_pct": full["global"]["agree_pct"], "n": full["global"]["n_agree_known"]},
            "pool_ge3": {"agree_pct": full["pool_ge3"]["agree_pct"], "n": full["pool_ge3"]["n_agree_known"]},
            "pool_le2": {"agree_pct": full["pool_le2"]["agree_pct"], "n": full["pool_le2"]["n_agree_known"]},
            "pool_unknown": {"agree_pct": full["pool_unknown"]["agree_pct"],
                             "n": full["pool_unknown"]["n_agree_known"]},
            "scarcity": full["scarcity_decomposition"],
            "pool_coverage_pct": full["pool_coverage_pct"],
            "window": l2["window"],
            "pool_known_window": l2["pool_known_window"],
            "overlap": ({"agree_pct": l2["overlap"]["global"]["agree_pct"],
                         "n": l2["overlap"]["global"]["n_agree_known"],
                         "label": l2["overlap"]["label"]} if "overlap" in l2 else None),
            "reason": None,
        }
    else:
        rec["liczba2"] = {"metric": "agree_top1_by_pool", "global": None, "reason": l2.get("reason")}
        rec["status"] = "DEGRADED"
        rec["degraded"].append(f"liczba2: {l2.get('reason')}")

    if join.get("available"):
        rec["join"] = {"n_joined": join["n_joined"], "matrix": join["matrix"],
                       "agree_given_auto_ready_pct": join["agree_given_auto_ready_pct"],
                       "agree_given_not_auto_ready_pct": join["agree_given_not_auto_ready_pct"],
                       "reason": None}
    else:
        rec["join"] = {"n_joined": None, "reason": join.get("reason")}
    return rec


def append_series(series_path, record):
    """APPEND-ONLY: dopisanie jednej linii, fsync pliku i katalogu. Nigdy "w", nigdy kasowanie."""
    parent = os.path.dirname(series_path) or "."
    os.makedirs(parent, exist_ok=True)
    line = json.dumps(record, ensure_ascii=False, sort_keys=False) + "\n"
    with open(series_path, "a", encoding="utf-8") as fh:
        fh.write(line)
        fh.flush()
        os.fsync(fh.fileno())
    return len(line)


def write_atomic(path, text):
    """Zapis atomowy w obrębie katalogu raportu: temp → fsync → rename."""
    parent = os.path.dirname(path) or "."
    os.makedirs(parent, exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        fh.write(text)
        fh.flush()
        os.fsync(fh.fileno())
    os.rename(tmp, path)


# ───────────────────────── raport dnia (MD) ─────────────────────────

def render_md(day, l1, l2, join, manifest, started, finished, series_path):
    L = []
    A = L.append
    A(f"# SUWAK AUTONOMII ZIOMKA — DWIE LICZBY ({day})")
    A("")
    A(f"**Wygenerowano:** {finished} · **Tryb:** READ-ONLY (zero zapisu do stanu, flag, telegramu, restartów)")
    A(f"**Czytelnik:** `dispatch_v2/tools/suwak_autonomii_review.py` (dobowy, pod `shadow-review.timer`)")
    A(f"**Szereg czasowy:** `{series_path}`")
    A("")
    A("---")
    A("")
    A("## ⭐ ODPOWIEDŹ W DWÓCH LICZBACH")
    A("")
    if l1.get("available"):
        g1 = l1["global"]
        A(f"**LICZBA 1 — ile Ziomek zrobiłby SAM:** **{fmt_pct(g1['would_auto_assign_d']['pct'])}** decyzji "
          f"kwalifikuje się do auto-assign w wariancie D (n={g1['would_auto_assign_d']['n']}, "
          f"okno {l1['window']['day_first']}..{l1['window']['day_last']}, {l1['window']['days_distinct']} dni).")
        A(f"Dla porównania: bazowy (dzisiejsze bramki) **{fmt_pct(g1['would_auto_assign']['pct'])}**, "
          f"wariant D' **{fmt_pct(g1['would_auto_assign_dprime']['pct'])}**, "
          f"kanał `auto_route=AUTO` **{fmt_pct(g1['auto_route_AUTO']['pct'])}**.")
    else:
        A(f"**LICZBA 1 — BRAK POMIARU.** Powód: {l1.get('reason')}")
    A("")
    if l2.get("available"):
        full = l2["full"]
        le2, ge3 = full["pool_le2"], full["pool_ge3"]
        A(f"**LICZBA 2 — ile niezgody to REDYSTRYBUCJA, a nie błąd:** przy niedoborze floty (pool<=2) "
          f"zgodność spada do **{fmt_pct(le2['agree_pct'])}** (n={le2['n_agree_known']}), przy realnym "
          f"wyborze (pool>=3) wynosi **{fmt_pct(ge3['agree_pct'])}** (n={ge3['n_agree_known']}). "
          f"Globalnie **{fmt_pct(full['global']['agree_pct'])}** (n={full['global']['n_agree_known']}).")
        sc = full.get("scarcity_decomposition")
        if sc:
            A("")
            A(f"**Rozkład przyczynowy segmentu pool<=2:** stopa niezgody {fmt_pct(sc['le2_disagree_rate_pct'], 1)}, "
              f"z czego {fmt_pct(sc['baseline_disagree_rate_pct'], 1)} to poziom bazowy (taki sam jak przy "
              f"pool>=3, czyli niezależny od floty), a reszta to **nadwyżka z niedoboru**: "
              f"**{fmt_pct(sc['excess_share_of_le2_disagreements_pct'], 1)} niezgód w tym segmencie jest "
              f"wymuszone brakiem kurierów** ({sc['le2_disagree_excess_scarcity']} z {sc['le2_disagree_observed']}).")
            A(f"W przeliczeniu na CAŁY zmierzony korpus (pula znana, n={sc['measured_disagreements']} niezgód): "
              f"**{fmt_pct(sc['excess_share_of_measured_disagreements_pct'], 1)} niezgody to redystrybucja**, "
              "reszta to realna różnica wyboru — i to ona jest materiałem do nauki.")
    else:
        A(f"**LICZBA 2 — BRAK POMIARU.** Powód: {l2.get('reason')}")
    A("")
    A("---")
    A("")
    A("## LICZBA 1 — gotowość auto-assign (`shadow_decisions.jsonl` + rotacje)")
    A("")
    if l1.get("available"):
        g1 = l1["global"]
        A(f"Okno: **{l1['window']['day_first']} .. {l1['window']['day_last']}** "
          f"({l1['window']['days_distinct']} dni). Mianownik = decyzje dyspozytorskie po odfiltrowaniu "
          "rekordów lifecycle.")
        A("")
        A("| Miara | % | true / n |")
        A("|---|---|---|")
        for key, label in (("would_auto_assign", "bazowy `would_auto_assign` (dzisiejsze bramki)"),
                           ("would_auto_assign_d", "**wariant D `would_auto_assign_d`**"),
                           ("would_auto_assign_dprime", "wariant D' `would_auto_assign_dprime`")):
            s = g1[key]
            A(f"| {label} | {fmt_pct(s['pct'])} | {s['true']} / {s['n']} |")
        s = g1["auto_route_AUTO"]
        A(f"| kanał `auto_route == AUTO` | {fmt_pct(s['pct'])} | {s['true']} / {s['n']} |")
        A("")
        A(f"Rozkład kanału `auto_route`: `{g1['auto_route_dist']}`")
        A("")
        A("### Rozbicie wg puli wykonalnych kurierów (`pool_feasible_count`)")
        A("")
        A("| Pula | n decyzji | bazowy | D | D' | auto_route=AUTO |")
        A("|---|---|---|---|---|---|")
        for key, label in (("pool_ge3", "pool>=3"), ("pool_le2", "pool<=2"), ("pool_unknown", "nieznana")):
            b = l1[key]
            A(f"| {label} | {b['n']} | {fmt_pct(b['would_auto_assign']['pct'])} | "
              f"{fmt_pct(b['would_auto_assign_d']['pct'])} | {fmt_pct(b['would_auto_assign_dprime']['pct'])} | "
              f"{fmt_pct(b['auto_route_AUTO']['pct'])} |")
        A("")
        bl = l1["blockers_d"]
        A(f"### Co blokuje pozostałe {bl['n_blocked']} decyzji (wariant D)")
        A("")
        A(f"Rekordy z zapisanym powodem: {bl['n_with_reasons']} / {bl['n_blocked']}. "
          "Rodziny powodów (rekord może mieć kilka; liczone unikalnie na rekord):")
        A("")
        A("| Rodzina powodu | ile rekordów |")
        A("|---|---|")
        for k, v in bl["families_any"].items():
            A(f"| `{k}` | {v} |")
    else:
        A(f"Brak pomiaru: {l1.get('reason')}")
    A("")
    A("---")
    A("")
    A("## LICZBA 2 — zgodność A1: redystrybucja vs realny błąd (`outcomes_clean_shadow.jsonl`)")
    A("")
    if l2.get("available"):
        A(f"Okno: **{l2['window']['day_first']} .. {l2['window']['day_last']}** "
          f"({l2['window']['days_distinct']} dni).")
        A("`agree` = kurier nr 1 w rankingu Ziomka **jest tym, który realnie zawiózł** "
          "(`best_courier_id == real_courier_id`, writer `shadow_collectors.py`).")
        A("")
        pk = l2["pool_known_window"]
        A(f"> ⚠ **Globalna zgodność obejmuje {l2['window']['days_distinct']} dni, ale ROZBICIE NA PULĘ tylko "
          f"{pk['days_distinct']} dni** ({pk['day_first']}..{pk['day_last']}, {pk['records']} rek. = "
          f"{fmt_pct(pk['share_of_corpus_pct'], 1)} korpusu). Pole `pool_feasible` jest dopisywane wyłącznie "
          "tam, gdzie istnieje join z `backfill_decisions_outcomes_v1.jsonl`, a ten sięga płycej niż cały "
          "korpus. Cytowanie globalnej zgodności i rozbicia na pulę jako JEDNEGO pomiaru jest błędem.")
        A("")
        for scope_key in ("full", "overlap"):
            if scope_key not in l2:
                continue
            sc = l2[scope_key]
            A(f"### {sc['label']}")
            A("")
            A("| Zakres | n (agree znane) | zgodność | niezgoda |")
            A("|---|---|---|---|")
            for key, label in (("global", "globalnie"), ("pool_ge3", "**pool>=3** (był wybór)"),
                               ("pool_le2", "**pool<=2** (niedobór floty)"), ("pool_unknown", "pula nieznana")):
                s = sc[key]
                A(f"| {label} | {s['n_agree_known']} | {fmt_pct(s['agree_pct'])} | {fmt_pct(s['disagree_pct'])} |")
            sp = sc["disagreement_split"]
            A("")
            A(f"Pokrycie pola `pool_feasible`: **{fmt_pct(sc['pool_coverage_pct'], 1)}** rekordów "
              "(reszta = brak joinu z backfillem, liczona osobno jako \"pula nieznana\").")
            A(f"Rozkład {sp['total']} niezgód: pool<=2 → **{sp['from_pool_le2']}** "
              f"({fmt_pct(sp['from_pool_le2_pct'], 1)}), pool>=3 → {sp['from_pool_ge3']} "
              f"({fmt_pct(sp['from_pool_ge3_pct'], 1)}), pula nieznana → {sp['from_pool_unknown']} "
              f"({fmt_pct(sp['from_pool_unknown_pct'], 1)}).")
            cov = sc["pool_coverage_pct"]
            if cov is not None and cov < 50:
                A("")
                A(f"> ⚠ **Nie cytuj tego rozkładu** — przy pokryciu {fmt_pct(cov, 1)} kubełek \"pula nieznana\" "
                  "dominuje i udziały pool<=2/pool>=3 są zaniżone mechanicznie, nie merytorycznie.")
            d = sc.get("scarcity_decomposition")
            if d:
                A("")
                A("**Redystrybucja vs realny błąd (segment pool<=2):**")
                A("")
                A("| Składnik | wartość |")
                A("|---|---|")
                A(f"| stopa niezgody przy pool<=2 | {fmt_pct(d['le2_disagree_rate_pct'], 1)} |")
                A(f"| poziom bazowy (stopa przy pool>=3) | {fmt_pct(d['baseline_disagree_rate_pct'], 1)} |")
                A(f"| niezgody obserwowane przy pool<=2 | {d['le2_disagree_observed']} |")
                A(f"| ile byłoby przy poziomie bazowym | {d['le2_disagree_expected_at_baseline']} |")
                A(f"| **nadwyżka = redystrybucja z niedoboru** | **{d['le2_disagree_excess_scarcity']}** "
                  f"({fmt_pct(d['excess_share_of_le2_disagreements_pct'], 1)} niezgód segmentu) |")
                A(f"| udział redystrybucji w całym zmierzonym korpusie | "
                  f"{fmt_pct(d['excess_share_of_measured_disagreements_pct'], 1)} "
                  f"(z {d['measured_disagreements']} niezgód przy znanej puli) |")
            A("")
        A("### Trend tygodniowy")
        A("")
        A("| Tydzień | n | zgodność | udział pool<=2 (wśród znanych) |")
        A("|---|---|---|---|")
        for t in l2["weekly_trend"]:
            A(f"| {t['week']} | {t['n']} | {fmt_pct(t['agree_pct'], 1)} | {fmt_pct(t['pool_le2_share_pct'], 1)} |")
    else:
        A(f"Brak pomiaru: {l2.get('reason')}")
    A("")
    A("---")
    A("")
    A("## JOIN OBU ŹRÓDEŁ — te same zamówienia")
    A("")
    if join.get("available") and join.get("n_joined"):
        m = join["matrix"]
        A(f"Wspólnych zamówień w oknie nakładania: **{join['n_joined']}** "
          f"(z {join['candidates_outcomes_in_window']} outcomes w oknie i "
          f"{join['decisions_indexed']} zindeksowanych decyzji).")
        A("")
        A("| | człowiek zgodny | człowiek inny |")
        A("|---|---|---|")
        A(f"| **auto-gotowe (D)** | {m['auto=T,agree=T']} | {m['auto=T,agree=F']} |")
        A(f"| **nie auto-gotowe** | {m['auto=F,agree=T']} | {m['auto=F,agree=F']} |")
        A("")
        o = l1.get("orders", {}) if l1.get("available") else {}
        A(f"Join po `order_id`; zamówienie może mieć kilka decyzji (przekierowania) — brana jest "
          f"**najwcześniejsza w czasie** (najmniejszy `ts`, także gdy leży w innej rotacji niż "
          f"pozostałe). Zamówień z >1 decyzją: {o.get('with_multiple_decisions')} "
          f"z {o.get('distinct')} (max {o.get('max_decisions_per_order')} decyzji na zamówienie).")
        A("")
        A(f"Zgodność **wśród decyzji auto-gotowych (D)**: {fmt_pct(join['agree_given_auto_ready_pct'])} "
          f"vs {fmt_pct(join['agree_given_not_auto_ready_pct'])} wśród nie-auto-gotowych. "
          "To bezpośredni test tezy \"łatwe = auto\".")
    else:
        A(f"Join niedostępny: {join.get('reason') or 'brak wspólnych zamówień w oknie'}")
    A("")
    A("---")
    A("")
    A("## UCZCIWOŚĆ POMIARU — czego te liczby NIE mówią")
    A("")
    A("1. **Różne okna.** Liczba 1 sięga tylko tak głęboko, jak pozwala rotacja "
      "`shadow_decisions.jsonl` (logrotate daily/30/100M) — okno bywa krótsze z dnia na dzień. "
      "Liczba 2 idzie po całym korpusie outcomes. Sekcja \"okno wspólne\" przycina Liczbę 2 do "
      "okna Liczby 1 — TE wartości są porównywalne, globalna nie.")
    A("2. **Pokrycie `pool_feasible` w outcomes jest częściowe** i pochodzi z joinu po `order_id` z "
      "`backfill_decisions_outcomes_v1.jsonl`; brak joinu = \"pula nieznana\", raportowana osobno, "
      "NIE doliczana do żadnego kubełka. Globalna zgodność i rozbicie na pulę to DWA RÓŻNE OKNA.")
    A("3. **`agree` to zgodność TOP-1, nie miara jakości.** Fałsz oznacza tylko, że zawiózł ktoś inny "
      "niż nr 1 Ziomka — nie przesądza, kto wybrał lepiej.")
    if l1.get("available"):
        it = l1["intake"]
        A(f"4. **Wykluczenia z mianownika Liczby 1:** {it['excluded_lifecycle']} rekordów lifecycle "
          f"(`CZASOWKA_RECLAIM_EVALUATION`), {it['excluded_missing_gate_fields']} bez pól bramki auto, "
          f"{it['duplicates_dropped']} duplikatów po `event_id`, {it['bad_lines']} nieparsowalnych linii.")
    else:
        A("4. **Wykluczenia z mianownika Liczby 1:** n/d — liczba nie powstała.")
    A("5. **`would_auto_assign_*` to symulacja cienia, nie wykonanie.** Auto-assign jest OFF; te pola "
      "mówią \"tyle by przeszło bramkę\", a nie \"tyle Ziomek zrobił\".")
    A("6. **Żywy plik.** `shadow_decisions.jsonl` jest dopisywany na bieżąco; SHA-256 w manifeście "
      f"opisuje stan z momentu odczytu ({started}).")
    A("")
    A("## MANIFEST WEJŚĆ")
    A("")
    A("| Plik | rekordy | złe linie | okno | rozmiar | mtime (UTC) | SHA-256 |")
    A("|---|---|---|---|---|---|---|")
    for m in manifest:
        A(f"| `{m.get('path')}` | {m.get('records', '—')} | {m.get('bad_lines', '—')} | "
          f"{m.get('day_first')}..{m.get('day_last')} | {m.get('size_bytes', '—')} B | "
          f"{m.get('mtime_utc', '—')} | `{m.get('sha256', '—')}` |")
    A("")
    A(f"Start odczytu: {started} · koniec: {finished}")
    A("")
    return "\n".join(L) + "\n"


# ───────────────────────── main ─────────────────────────

def build_parser():
    ap = argparse.ArgumentParser(
        description="Dobowy czytelnik suwaka autonomii (2 liczby) — READ-ONLY, append-only szereg.")
    ap.add_argument("--out-dir", default=None,
                    help=f"katalog snapshotu dnia (default {DEFAULT_OUT_ROOT}/<UTC-data>)")
    ap.add_argument("--series-path", default=None,
                    help="plik szeregu czasowego (default <rodzic out-dir>/" + SERIES_FILENAME + ")")
    ap.add_argument("--decisions", default=DECISIONS_BASE_DEFAULT,
                    help="bazowy log decyzji; rotacje dobierane globem")
    ap.add_argument("--outcomes", default=OUTCOMES_DEFAULT, help="korpus outcomes cienia")
    ap.add_argument("--max-corpus-age-hours", type=float, default=DEFAULT_MAX_CORPUS_AGE_H,
                    help="powyżej tego wieku korpus = martwy, liczba NIE powstaje (null + powód)")
    ap.add_argument("--no-telegram", action="store_true",
                    help="bez efektu — ten czytelnik nie zna Telegrama; flaga dla jednolitości wywołania")
    ap.add_argument("--no-series", action="store_true",
                    help="nie dopisuj do szeregu (bieg ręczny/diagnostyczny)")
    return ap


def run(args):
    started_dt = _now()
    started = _iso(started_dt)
    day = started_dt.strftime("%Y-%m-%d")
    out_dir = args.out_dir or os.path.join(DEFAULT_OUT_ROOT, day)
    series_path = args.series_path or os.path.join(
        os.path.dirname(os.path.abspath(out_dir)), SERIES_FILENAME)

    manifest = []
    # FAIL-SOFT: każdy człon liczony osobno; wyjątek degraduje LICZBĘ, nie cały przegląd cienia.
    try:
        l1, man1, dec_idx = compute_liczba1(args.decisions, args.max_corpus_age_hours)
        manifest += man1
    except Exception as exc:
        warn(f"LICZBA 1 nie powstała: {type(exc).__name__}: {exc}")
        l1, dec_idx = {"available": False, "reason": f"{type(exc).__name__}: {exc}"}, {}

    window = (l1.get("window", {}).get("day_first"), l1.get("window", {}).get("day_last")) \
        if l1.get("available") else (None, None)

    try:
        l2, man2, out_rows = compute_liczba2(args.outcomes, args.max_corpus_age_hours, window=window)
        manifest.append(man2)
    except Exception as exc:
        warn(f"LICZBA 2 nie powstała: {type(exc).__name__}: {exc}")
        l2, out_rows = {"available": False, "reason": f"{type(exc).__name__}: {exc}"}, []

    try:
        join = compute_join(out_rows, dec_idx, window)
    except Exception as exc:
        warn(f"JOIN nie powstał: {type(exc).__name__}: {exc}")
        join = {"available": False, "reason": f"{type(exc).__name__}: {exc}"}

    finished = _iso(_now())
    snapshot_json = os.path.join(out_dir, SNAPSHOT_JSON)
    record = build_series_record(day, started, finished, l1, l2, join, manifest, snapshot_json)

    payload = {
        "schema": SNAPSHOT_SCHEMA,
        "day": day,
        "generated_utc": finished,
        "read_started_utc": started,
        "mode": "READ_ONLY",
        "status": record["status"],
        "series_path": None if args.no_series else series_path,
        "liczba1_auto_assign": l1,
        "liczba2_zgodnosc_redystrybucja": l2,
        "join_wspolne_zamowienia": join,
        "manifest": manifest,
    }
    # KOLEJNOŚĆ JEST KONTRAKTEM: najpierw WSZYSTKIE zapisy snapshotu, dopiero na końcu linia
    # szeregu. Szereg jest append-only, więc jego linii nie da się później poprawić — gdyby
    # powstała pierwsza, utrwaliłaby status "OK" i wskaźnik na snapshot, który nie powstał.
    snapshot_written = False
    try:
        write_atomic(snapshot_json, json.dumps(payload, ensure_ascii=False, indent=2))
        snapshot_written = True
        write_atomic(os.path.join(out_dir, SNAPSHOT_MD),
                     render_md(day, l1, l2, join, manifest, started, finished, series_path))
    except Exception as exc:
        warn(f"nie zapisano snapshotu dnia w {out_dir}: {type(exc).__name__}: {exc}")
        record["status"] = "DEGRADED"
        record["degraded"].append(f"snapshot: {type(exc).__name__}: {exc}")
        # Wskaźnik zostaje TYLKO gdy plik faktycznie istnieje (padł np. sam raport MD).
        if not snapshot_written:
            record["snapshot"] = None

    if not args.no_series:
        try:
            append_series(series_path, record)
        except Exception as exc:
            warn(f"nie dopisano do szeregu {series_path}: {type(exc).__name__}: {exc}")
            record["status"] = "DEGRADED"
            record["degraded"].append(f"series: {type(exc).__name__}")

    l1txt = (f"{fmt_pct(record['liczba1']['pct'])} auto(D) n={record['liczba1']['n']}"
             if record["liczba1"].get("pct") is not None else f"BRAK ({record['liczba1'].get('reason')})")
    if record["liczba2"].get("global"):
        l2v = record["liczba2"]
        l2txt = (f"zgodnosc pool<=2 {fmt_pct(l2v['pool_le2']['agree_pct'])} (n={l2v['pool_le2']['n']}) "
                 f"vs pool>=3 {fmt_pct(l2v['pool_ge3']['agree_pct'])} (n={l2v['pool_ge3']['n']})")
    else:
        l2txt = f"BRAK ({record['liczba2'].get('reason')})"
    print(f"➤ WERDYKT: SUWAK {record['status']} | LICZBA1 {l1txt} | LICZBA2 {l2txt}")
    print(f"szereg: {series_path if not args.no_series else '(pominięty --no-series)'}")
    print(f"snapshot: {snapshot_json}")
    return record


def main(argv=None):
    args = build_parser().parse_args(argv)
    try:
        run(args)
    except Exception as exc:  # FAIL-SOFT twardy: przegląd cienia ma dokończyć bieg
        warn(f"bieg suwaka przerwany: {type(exc).__name__}: {exc}")
        print(f"➤ WERDYKT: SUWAK DEGRADED | {type(exc).__name__}: {exc}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
