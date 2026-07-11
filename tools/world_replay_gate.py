#!/usr/bin/env python3
"""world_replay_gate — K17 programu refaktoru (bramka korpusowa, finał K06).

Iteruje po rekordach `world_record` z `now≠null` w zadanym oknie czasu, każdy
odtwarza logiką `tools/world_replay.replay_one` (pełny sandbox — zero sieci,
zero zapisów silnika) i porównuje z kanonicznym zapisem `shadow_decisions`
(join rotation-aware przez `ledger_io`, ±300 s po ts, najbliższy rekord).

Raport zbiorczy z rozłącznymi klasami INPUT_MISS/OSRM_MISS/CRITICAL_DIFF/
SOFT_DIFF/PARITY, stałym mianownikiem, coverage i freshness →
plik werdyktu (default `dispatch_state/world_replay_gate_verdict.txt`,
zapis atomowy) + stdout. Exit: 0 = korpus zgodny (≥1 replay, 0 różnic,
0 missów OSRM, 0 błędów); 1 = różnice/missy/błędy; 2 = puste okno / awaria
narzędzia. Night-guard w trybie INFORMACYJNYM ignoruje exit (K17c) —
eskalacja na egzekwujący = decyzja Adriana po 3 zielonych nocach.

Użycie:
  venvs/dispatch/bin/python -m dispatch_v2.tools.world_replay_gate \
      [--since ISO | --hours N] [--until ISO] [--max-n N] \
      [--record-dir DIR] [--out PLIK] [--json]
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

SCRIPTS = "/root/.openclaw/workspace/scripts"
if SCRIPTS not in sys.path:
    sys.path.insert(0, SCRIPTS)

from dispatch_v2.tools import world_replay as WR  # noqa: E402 (reużycie 1:1 — zero bliźniaka)

DEFAULT_OUT = "/root/.openclaw/workspace/dispatch_state/world_replay_gate_verdict.txt"
JOIN_TOLERANCE_SEC = 300.0  # ta sama tolerancja co world_replay.find_shadow

# Rekordy sprzed schematu wr1 (v0/wr0) NIE mają nagranych `live_inputs`
# (K07 prefetch czasu, loadgov EWMA, pliki kalibracji) — bit-w-bit replay ich
# NIE odtwarza (world_record.py: „Bit-w-bit replay wymaga rekordu wr1"). Replay
# liczy te wejścia od nowa w świeżym procesie → różnica jest LUKĄ NAGRYWANIA,
# nie bugiem determinizmu (diagnoza A2_worldreplay_minus40: kara loadgov −40 na
# rekordach wr0 = 12 fałszywych „ROZNICA-KRYTYCZNA"). Takie rekordy = POMINIĘTE
# (nie-certyfikowalne), raportowane osobno — nie mieszają się z realnymi różnicami
# na wr1. Zbiór NAZWANY i forward-compatible: wr2+ (gdyby powstał) przechodzi.
_PRE_WR1_SCHEMAS = frozenset({None, "wr0"})


def _iter_window_records(record_dir: str, since: datetime | None,
                         until: datetime | None):
    """Rekordy world_record z oknem [since, until]: tylko `now≠null` (replay
    wierny zegarowo — K06a) ORAZ tylko `schema=wr1` (faithfully-replayable —
    wr0/v0 bez `live_inputs` → POMINIĘTE); dedup po (order_id, ts), sort po ts.
    Zwraca (out, skipped_no_now, skipped_pre_wr1)."""
    seen = set()
    out = []
    skipped_no_now = 0
    skipped_pre_wr1 = 0
    for p in sorted(str(x) for x in Path(record_dir).glob("world_record-*.jsonl")):
        for rec in WR._iter_jsonl(p):
            ts = WR._parse_dt(rec.get("ts"))
            if ts is None:
                continue
            if since is not None and ts < since:
                continue
            if until is not None and ts > until:
                continue
            if not rec.get("now"):
                skipped_no_now += 1
                continue
            if rec.get("schema") in _PRE_WR1_SCHEMAS:
                skipped_pre_wr1 += 1
                continue
            key = (str(rec.get("order_id")), rec.get("ts"))
            if key in seen:
                continue
            seen.add(key)
            out.append(rec)
    out.sort(key=lambda r: r.get("ts") or "")
    return out, skipped_no_now, skipped_pre_wr1


def _scan_window_records(record_dir: str, since: datetime | None,
                         until: datetime | None):
    """Buduje staly mianownik: wszystkie unikalne rekordy z poprawnym ts.

    Brak ``now`` i schema<wr1 nie znikaja z mianownika — zostana jawnie
    sklasyfikowane jako INPUT_MISS. Poza mianownikiem sa tylko rekordy, ktorych
    nie da sie przypisac do okna, oraz duplikaty tego samego (order_id, ts).
    """
    seen = set()
    out = []
    skips = {"invalid_json": 0, "invalid_record": 0, "invalid_ts": 0,
             "duplicate": 0, "truncated_by_max_n": 0}
    for path in sorted(str(x) for x in Path(record_dir).glob("world_record-*.jsonl")):
        with open(path, encoding="utf-8") as source:
            raw_records = []
            for line in source:
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except json.JSONDecodeError:
                    skips["invalid_json"] += 1
                    continue
                if not isinstance(rec, dict):
                    skips["invalid_record"] += 1
                    continue
                raw_records.append(rec)
        for rec in raw_records:
            ts = WR._parse_dt(rec.get("ts"))
            if ts is None:
                skips["invalid_ts"] += 1
                continue
            if since is not None and ts < since:
                continue
            if until is not None and ts > until:
                continue
            key = (str(rec.get("order_id")), rec.get("ts"))
            if key in seen:
                skips["duplicate"] += 1
                continue
            seen.add(key)
            out.append(rec)
    out.sort(key=lambda rec: rec.get("ts") or "")
    return out, skips


def _input_miss_reason(rec: dict) -> str | None:
    """Pierwszy rozlaczny powod braku kompletnego frozen inputu."""
    checks = (
        (not rec.get("now") or WR._parse_dt(rec.get("now")) is None, "missing_now"),
        (rec.get("schema") in _PRE_WR1_SCHEMAS, "schema_pre_wr1"),
        (not isinstance(rec.get("order_event"), dict), "missing_order_event"),
        (not isinstance(rec.get("fleet"), dict), "missing_fleet"),
        (not isinstance(rec.get("flags"), dict), "missing_flags"),
        (not isinstance(rec.get("live_inputs"), dict), "missing_live_inputs"),
    )
    return next((reason for missing, reason in checks if missing), None)


def _record_ref(rec: dict) -> str:
    raw = f"{rec.get('order_id')}|{rec.get('ts')}".encode("utf-8", "replace")
    return hashlib.sha256(raw).hexdigest()[:12]


def _public_classification(ref: str, classified: dict) -> dict:
    """Redaguje wynik do artefaktu: pola różnic, nigdy ich wartosci/ID."""
    return {"record_ref": ref, "class": classified["class"],
            "reason": classified.get("reason"),
            "diff_fields": sorted(classified.get("diffs", {}))}


def _build_shadow_index(since: datetime | None, shadow_file: str | None = None):
    """Indeks {order_id: [rec,…]} z shadow_decisions — odczyt ROTATION-AWARE
    (`ledger_io.iter_shadow_decisions`, reguła C16: naiwny odczyt żywego pliku
    gubi okno po rotacji). Cutoff z zapasem tolerancji joinu."""
    cutoff = None
    if since is not None:
        cutoff = since - timedelta(seconds=JOIN_TOLERANCE_SEC)
    idx: dict = {}
    if shadow_file is not None:
        source = WR._iter_jsonl(shadow_file)
    else:
        from dispatch_v2.tools import ledger_io
        source = ledger_io.iter_shadow_decisions(cutoff)
    for rec in source:
        if cutoff is not None:
            ts = WR._parse_dt(rec.get("ts"))
            if ts is None or ts < cutoff:
                continue
        oid = str(rec.get("order_id"))
        idx.setdefault(oid, []).append(rec)
    return idx


def _join_shadow(idx: dict, order_id: str, ts_iso: str):
    """Najbliższy po |Δts| rekord shadow tego order_id w tolerancji joinu."""
    ts = WR._parse_dt(ts_iso)
    best, best_d = None, None
    for rec in idx.get(str(order_id), []):
        rts = WR._parse_dt(rec.get("ts"))
        if ts is None or rts is None:
            continue
        d = abs((rts - ts).total_seconds())
        if d > JOIN_TOLERANCE_SEC:
            continue
        if best_d is None or d < best_d:
            best, best_d = rec, d
    return best


def run_gate(since: datetime | None, until: datetime | None,
             record_dir: str = WR.RECORD_DIR, max_n: int | None = None,
             shadow_index: dict | None = None,
             shadow_file: str | None = None,
             as_of: datetime | None = None) -> dict:
    """Bieg bramki na oknie korpusu. Zwraca raport zbiorczy (dict)."""
    records, skip_reasons = _scan_window_records(record_dir, since, until)
    truncated = False
    if max_n is not None and len(records) > max_n:
        skip_reasons["truncated_by_max_n"] = len(records) - max_n
        records = records[:max_n]
        truncated = True
    if shadow_index is not None:
        idx = shadow_index
    elif shadow_file is not None:
        idx = _build_shadow_index(since, shadow_file)
    else:
        idx = _build_shadow_index(since)

    classifications = []
    for rec in records:
        oid = str(rec.get("order_id"))
        ref = _record_ref(rec)
        missing = _input_miss_reason(rec)
        if missing:
            classified = WR.classify_replay(None, None, input_miss_reason=missing)
            classifications.append(_public_classification(ref, classified))
            continue
        shadow = _join_shadow(idx, oid, rec.get("ts"))
        if shadow is None:
            classified = WR.classify_replay(
                None, None, input_miss_reason="shadow_record_missing")
            classifications.append(_public_classification(ref, classified))
            continue
        try:
            replayed, n_miss = WR.replay_one(rec)
        except Exception as e:  # jeden zepsuty rekord nie zabija biegu korpusu
            classified = WR.classify_replay(
                None, None, input_miss_reason=f"replay_error:{type(e).__name__}")
            classifications.append(_public_classification(ref, classified))
            continue
        recorded = WR._extract(shadow)
        classified = WR.classify_replay(recorded, replayed, n_miss)
        classifications.append(_public_classification(ref, classified))

    n = len(records)
    counts = {cls: 0 for cls in WR.REPLAY_CLASSES}
    for item in classifications:
        counts[item["class"]] += 1
    if sum(counts.values()) != n:
        raise RuntimeError("replay classification is not exhaustive")

    input_reason_counts = {}
    for item in classifications:
        if item["class"] == "INPUT_MISS":
            reason = item["reason"] or "unknown"
            input_reason_counts[reason] = input_reason_counts.get(reason, 0) + 1
    input_complete_n = n - counts["INPUT_MISS"]
    comparable_n = counts["CRITICAL_DIFF"] + counts["SOFT_DIFF"] + counts["PARITY"]
    newest = max((WR._parse_dt(rec.get("ts")) for rec in records), default=None)
    reference = as_of or until or datetime.now(timezone.utc)
    if reference.tzinfo is None:
        reference = reference.replace(tzinfo=timezone.utc)
    freshness_age = (max(0.0, (reference - newest).total_seconds())
                     if newest is not None else None)
    stable_rows = [
        f"{item['record_ref']}:{item['class']}:{item.get('reason') or ''}:"
        f"{','.join(item.get('diff_fields', []))}"
        for item in classifications
    ]
    corpus_fingerprint = hashlib.sha256("\n".join(stable_rows).encode()).hexdigest()

    roznice = [
        {"record_ref": item["record_ref"],
         "krytyczna": item["class"] == "CRITICAL_DIFF",
         "diff_fields": item["diff_fields"]}
        for item in classifications if item["class"] in {"CRITICAL_DIFF", "SOFT_DIFF"}
    ]
    missy = [{"record_ref": item["record_ref"]} for item in classifications
             if item["class"] == "OSRM_MISS"]
    brak_zapisu = [{"record_ref": item["record_ref"]} for item in classifications
                   if item.get("reason") == "shadow_record_missing"]
    bledy = [{"record_ref": item["record_ref"], "error_type": item["reason"].split(":", 1)[-1]}
             for item in classifications
             if (item.get("reason") or "").startswith("replay_error:")]
    report = {
        "evaluated_at": reference.isoformat(),
        "window": {"since": since.isoformat() if since else None,
                   "until": until.isoformat() if until else None,
                   "record_dir": record_dir, "truncated_to_max_n": truncated},
        "denominator": n,
        "class_counts": counts,
        "input_miss_reasons": input_reason_counts,
        "skip_reasons": skip_reasons,
        "coverage": {
            "input_pct": round(100.0 * input_complete_n / n, 3) if n else 0.0,
            "osrm_pct": (round(100.0 * comparable_n / input_complete_n, 3)
                         if input_complete_n else 0.0),
            "oracle_pct": round(100.0 * comparable_n / n, 3) if n else 0.0,
        },
        "freshness": {"newest_record_at": newest.isoformat() if newest else None,
                      "age_seconds": freshness_age},
        "corpus_fingerprint": corpus_fingerprint,
        "classifications": classifications,
        "n": n,
        "zgodne": counts["PARITY"],
        "roznice_n": len(roznice),
        "roznice_krytyczne_n": counts["CRITICAL_DIFF"],
        "roznice_miekkie_n": counts["SOFT_DIFF"],
        "missy_n": len(missy),
        "brak_zapisu_n": len(brak_zapisu),
        "bledy_n": len(bledy),
        "skipped_no_now": input_reason_counts.get("missing_now", 0),
        "skipped_pre_wr1": input_reason_counts.get("schema_pre_wr1", 0),
        "roznice": roznice,
        "missy": missy,
        "brak_zapisu": brak_zapisu,
        "bledy": bledy,
    }
    report["verdict"] = _verdict(report)
    return report


def _verdict(report: dict) -> str:
    if report["denominator"] == 0:
        return "EMPTY_WINDOW"
    if any(report["class_counts"][cls] for cls in WR.REPLAY_CLASSES[:-1]):
        return "DIFFS"
    return "PARITY"


def render_verdict_txt(report: dict) -> str:
    w = report["window"]
    lines = [
        "world_replay_gate — bramka korpusowa (K17 / finał K06)",
        f"evaluated_at: {report['evaluated_at']}",
        f"okno: since={w['since']} until={w['until']}",
        f"WERDYKT: {report['verdict']}",
        f"denominator={report['denominator']} classes={json.dumps(report['class_counts'], sort_keys=True)}",
        f"coverage={json.dumps(report['coverage'], sort_keys=True)}",
        f"freshness={json.dumps(report['freshness'], sort_keys=True)}",
        f"input_miss_reasons={json.dumps(report['input_miss_reasons'], sort_keys=True)}",
        f"skip_reasons={json.dumps(report['skip_reasons'], sort_keys=True)}",
        f"corpus_fingerprint={report['corpus_fingerprint']}",
    ]
    for item in report["classifications"]:
        if item["class"] == "PARITY":
            continue
        detail = item.get("diff_fields") or item.get("reason")
        lines.append(f"{item['class']} ref={item['record_ref']} detail={detail}")
    return "\n".join(lines) + "\n"


def _write_atomic(path: str, content: str) -> None:
    d = os.path.dirname(path) or "."
    fd, tmp = tempfile.mkstemp(dir=d, prefix=".wrg-")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(content)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--since", help="ISO początek okna (UTC gdy bez strefy)")
    ap.add_argument("--until", help="ISO koniec okna")
    ap.add_argument("--hours", type=float,
                    help="okno = ostatnie N godzin (alternatywa dla --since)")
    ap.add_argument("--max-n", type=int, default=None)
    ap.add_argument("--record-dir", default=WR.RECORD_DIR)
    ap.add_argument("--shadow-file",
                    help="jawny JSONL ledgera (test/offline); brak = rotation-aware live reader")
    ap.add_argument("--as-of", help="stala kotwica freshness/determinizmu")
    ap.add_argument("--out", default=DEFAULT_OUT,
                    help=f"plik werdyktu (default {DEFAULT_OUT})")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)

    since = WR._parse_dt(a.since) if a.since else None
    until = WR._parse_dt(a.until) if a.until else None
    if since is None and a.hours:
        since = datetime.now(timezone.utc) - timedelta(hours=a.hours)
    if since is not None and since.tzinfo is None:
        since = since.replace(tzinfo=timezone.utc)
    if until is not None and until.tzinfo is None:
        until = until.replace(tzinfo=timezone.utc)
    as_of = WR._parse_dt(a.as_of) if a.as_of else None
    if as_of is not None and as_of.tzinfo is None:
        as_of = as_of.replace(tzinfo=timezone.utc)

    try:
        report = run_gate(since, until, record_dir=a.record_dir, max_n=a.max_n,
                          shadow_file=a.shadow_file, as_of=as_of)
    except Exception as e:
        print(f"world_replay_gate: awaria narzędzia: {type(e).__name__}: {e}")
        return 2

    txt = render_verdict_txt(report)
    try:
        _write_atomic(a.out, txt)
    except Exception as e:
        print(f"world_replay_gate: zapis werdyktu nieudany ({a.out}): {e}")
        return 2
    if a.json:
        print(json.dumps(report, ensure_ascii=False, indent=1, default=str))
    else:
        print(txt, end="")
    return {"PARITY": 0, "DIFFS": 1, "EMPTY_WINDOW": 2}[report["verdict"]]


if __name__ == "__main__":
    sys.exit(main())
