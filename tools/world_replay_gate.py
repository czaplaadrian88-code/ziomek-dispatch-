#!/usr/bin/env python3
"""world_replay_gate — K17 programu refaktoru (bramka korpusowa, finał K06).

Iteruje po rekordach `world_record` z `now≠null` w zadanym oknie czasu, każdy
odtwarza logiką `tools/world_replay.replay_one` (pełny sandbox — zero sieci,
zero zapisów silnika) i porównuje z kanonicznym zapisem `shadow_decisions`
(join rotation-aware przez `ledger_io`, ±300 s po ts, najbliższy rekord).

Raport zbiorczy {n, zgodne, różnice, missy, brak_zapisu, błędy} →
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

# Różnica KRYTYCZNA = zmienia decyzję (werdykt/kurier/score). Miękka (reason,
# pool_*) = tekst/rozmiar puli — typowo wierność żywych plików nienagranych w
# world_record v0 (kalibracje/stan poza fleet+osrm+flags+now); raportowana
# osobno, ale nadal nie-zielona (exit≠0) — ocena należy do Adriana.
CORE_FIELDS = ("verdict", "best_cid", "best_score")


def _iter_window_records(record_dir: str, since: datetime | None,
                         until: datetime | None):
    """Rekordy world_record z oknem [since, until]: tylko `now≠null` (replay
    wierny zegarowo — K06a), dedup po (order_id, ts), posortowane po ts."""
    seen = set()
    out = []
    skipped_no_now = 0
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
            key = (str(rec.get("order_id")), rec.get("ts"))
            if key in seen:
                continue
            seen.add(key)
            out.append(rec)
    out.sort(key=lambda r: r.get("ts") or "")
    return out, skipped_no_now


def _build_shadow_index(since: datetime | None):
    """Indeks {order_id: [rec,…]} z shadow_decisions — odczyt ROTATION-AWARE
    (`ledger_io.iter_shadow_decisions`, reguła C16: naiwny odczyt żywego pliku
    gubi okno po rotacji). Cutoff z zapasem tolerancji joinu."""
    from dispatch_v2.tools import ledger_io
    cutoff = None
    if since is not None:
        cutoff = since - timedelta(seconds=JOIN_TOLERANCE_SEC)
    idx: dict = {}
    for rec in ledger_io.iter_shadow_decisions(cutoff):
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
             shadow_index: dict | None = None) -> dict:
    """Bieg bramki na oknie korpusu. Zwraca raport zbiorczy (dict)."""
    records, skipped_no_now = _iter_window_records(record_dir, since, until)
    truncated = False
    if max_n is not None and len(records) > max_n:
        records = records[:max_n]
        truncated = True
    idx = shadow_index if shadow_index is not None else _build_shadow_index(since)

    zgodne = 0
    roznice = []
    missy = []
    brak_zapisu = []
    bledy = []
    for rec in records:
        oid = str(rec.get("order_id"))
        try:
            replayed, n_miss = WR.replay_one(rec)
        except Exception as e:  # jeden zepsuty rekord nie zabija biegu korpusu
            bledy.append({"order_id": oid, "ts": rec.get("ts"),
                          "error": f"{type(e).__name__}: {e}"})
            continue
        shadow = _join_shadow(idx, oid, rec.get("ts"))
        if shadow is None:
            brak_zapisu.append({"order_id": oid, "ts": rec.get("ts")})
            continue
        recorded = WR._extract(shadow)
        diffs = {k: {"replay": replayed[k], "zapis": recorded[k]}
                 for k in replayed if replayed[k] != recorded[k]}
        if n_miss:
            missy.append({"order_id": oid, "ts": rec.get("ts"),
                          "osrm_misses": n_miss})
        if diffs:
            krytyczna = any(k in diffs for k in CORE_FIELDS)
            roznice.append({"order_id": oid, "ts": rec.get("ts"),
                            "krytyczna": krytyczna, "diffs": diffs})
        elif not n_miss:
            zgodne += 1

    n = len(records)
    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "window": {"since": since.isoformat() if since else None,
                   "until": until.isoformat() if until else None,
                   "record_dir": record_dir, "truncated_to_max_n": truncated},
        "n": n,
        "zgodne": zgodne,
        "roznice_n": len(roznice),
        "roznice_krytyczne_n": sum(1 for r in roznice if r["krytyczna"]),
        "roznice_miekkie_n": sum(1 for r in roznice if not r["krytyczna"]),
        "missy_n": len(missy),
        "brak_zapisu_n": len(brak_zapisu),
        "bledy_n": len(bledy),
        "skipped_no_now": skipped_no_now,
        "roznice": roznice,
        "missy": missy,
        "brak_zapisu": brak_zapisu,
        "bledy": bledy,
    }
    report["verdict"] = _verdict(report)
    return report


def _verdict(report: dict) -> str:
    if report["n"] == 0:
        return "EMPTY_WINDOW"
    if report["roznice_n"] or report["missy_n"] or report["bledy_n"]:
        return "DIFFS"
    if report["zgodne"] == 0:
        return "EMPTY_WINDOW"  # same brak_zapisu — nie ma czego certyfikować
    return "PARITY"


def render_verdict_txt(report: dict) -> str:
    w = report["window"]
    lines = [
        "world_replay_gate — bramka korpusowa (K17 / finał K06)",
        f"generated_at: {report['generated_at']}",
        f"okno: since={w['since']} until={w['until']}",
        f"WERDYKT: {report['verdict']}",
        (f"n={report['n']} zgodne={report['zgodne']} "
         f"roznice={report['roznice_n']} "
         f"(krytyczne={report['roznice_krytyczne_n']} "
         f"miekkie={report['roznice_miekkie_n']}) missy={report['missy_n']} "
         f"brak_zapisu={report['brak_zapisu_n']} bledy={report['bledy_n']} "
         f"(pominiete now=null: {report['skipped_no_now']})"),
    ]
    for r in report["roznice"]:
        tag = "ROZNICA-KRYTYCZNA" if r["krytyczna"] else "roznica-miekka"
        lines.append(f"{tag} order={r['order_id']} ts={r['ts']}: {json.dumps(r['diffs'], ensure_ascii=False, default=str)}")
    for m in report["missy"]:
        lines.append(f"MISS order={m['order_id']} ts={m['ts']} osrm_misses={m['osrm_misses']}")
    for b in report["brak_zapisu"]:
        lines.append(f"BRAK_ZAPISU order={b['order_id']} ts={b['ts']}")
    for e in report["bledy"]:
        lines.append(f"BLAD order={e['order_id']} ts={e['ts']}: {e['error']}")
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

    try:
        report = run_gate(since, until, record_dir=a.record_dir, max_n=a.max_n)
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
