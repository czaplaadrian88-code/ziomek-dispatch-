#!/usr/bin/env python3
"""
Krok 2 bundling-bias: pull score-margins best↔sib dla TOWARD+AWAY case'ów.

Dla każdego case'a z seed-korpusu (flip ∈ {TOWARD, AWAY}):
  - znajdź rekord shadow_decisions.jsonl dla oid najbliższy ts probe'a
  - winner = best (kogo Ziomek wybrał), winner_score
  - sib_cid: szukaj wśród {best, alternatives} (courier_id w alts=STRING, w best=INT → normalizuj str)
      * sib FEASIBLE (w serializacji) → sib_score, margin = winner_score − sib_score
        (margin = bonus potrzebny by flipnąć winner→sib)
      * sib NIE w serializacji → INFEASIBLE w chwili decyzji → bonus NIE pomoże (non-starter)

Wynik kalibracji:
  - TOWARD: ile sib-feasible (adresowalne) + rozkład margin (jaki bonus flipuje)
  - AWAY: margin = górne ograniczenie (bonus MUSI być < niż margin AWAY by nie psuć)
  - Cel: coeff/cap który flipuje max TOWARD-feasible bez przekroczenia min(AWAY margin)
"""
import json
import re
from datetime import datetime

CORPUS = "/root/.openclaw/workspace/scripts/dispatch_v2/eod_drafts/2026-05-31/bundling_bias_seed_corpus.json"
DECISIONS = "/root/.openclaw/workspace/scripts/logs/shadow_decisions.jsonl"


def parse_ts(s):
    # probe ts: "2026-05-30 13:23:43" ; decision ts: ISO "2026-05-30T13:23:43.81+00:00"
    s = s.strip().replace("T", " ")
    s = re.sub(r"[+Z].*$", "", s)
    s = s.split(".")[0]
    return datetime.strptime(s[:19], "%Y-%m-%d %H:%M:%S")


def norm(cid):
    return str(cid) if cid is not None else None


def load_corpus_targets():
    d = json.load(open(CORPUS))
    return [r for r in d["rows"] if r["flip"] in ("TOWARD", "AWAY")]


def index_decisions(oids):
    """oid -> list of (ts_dt, record) ; tylko interesujące oid."""
    want = set(oids)
    idx = {}
    with open(DECISIONS) as f:
        for ln in f:
            # szybki pre-filtr po stringu oid
            if not any(o in ln for o in want):
                continue
            try:
                rec = json.loads(ln)
            except json.JSONDecodeError:
                continue
            oid = str(rec.get("order_id"))
            if oid not in want:
                continue
            idx.setdefault(oid, []).append((parse_ts(rec["ts"]), rec))
    return idx


def nearest_record(recs, probe_dt):
    return min(recs, key=lambda x: abs((x[0] - probe_dt).total_seconds()))[1]


def candidate_score(rec, cid):
    """zwróć (score, found) dla courier cid wśród best+alternatives."""
    cid = norm(cid)
    b = rec.get("best") or {}
    if norm(b.get("courier_id")) == cid:
        return b.get("score"), True
    for a in rec.get("alternatives", []):
        if norm(a.get("courier_id")) == cid:
            return a.get("score"), True
    return None, False


def main():
    targets = load_corpus_targets()
    idx = index_decisions([t["oid"] for t in targets])
    out = []
    for t in targets:
        oid = t["oid"]
        recs = idx.get(oid)
        row = dict(oid=oid, flip=t["flip"], rest=t["rest"], sib_cid=t["sib_cid"],
                   sib_pos_source=t["sib_pos_source"], sib_bag=t["sib_bag_size"],
                   override=t["override"])
        if not recs:
            row.update(matched=False, note="brak rekordu shadow_decisions")
            out.append(row); continue
        probe_dt = parse_ts(t["ts"])
        rec = nearest_record(recs, probe_dt)
        rec_dt = parse_ts(rec["ts"])
        winner = rec.get("best") or {}
        winner_cid = norm(winner.get("courier_id"))
        winner_score = winner.get("score")
        sib_score, sib_found = candidate_score(rec, t["sib_cid"])
        row.update(
            matched=True,
            ts_skew_s=round((rec_dt - probe_dt).total_seconds(), 1),
            rec_verdict=rec.get("verdict"),
            pool_feasible=rec.get("pool_feasible_count"),
            winner_cid=winner_cid, winner_score=winner_score,
            sib_feasible=sib_found,
            sib_score=sib_score,
            margin=(round(winner_score - sib_score, 2)
                    if (sib_found and winner_score is not None and sib_score is not None)
                    else None),
        )
        out.append(row)

    # agregaty
    def bucket(flip):
        return [r for r in out if r.get("flip") == flip and r.get("matched")]
    tow, awy = bucket("TOWARD"), bucket("AWAY")
    tow_feas = [r for r in tow if r["sib_feasible"]]
    tow_infeas = [r for r in tow if not r["sib_feasible"]]
    awy_feas = [r for r in awy if r["sib_feasible"]]
    margins_tow = sorted(r["margin"] for r in tow_feas if r["margin"] is not None)
    margins_awy = sorted(r["margin"] for r in awy_feas if r["margin"] is not None)

    summary = dict(
        n_targets=len(targets),
        TOWARD=dict(n=len(tow), sib_feasible=len(tow_feas), sib_infeasible=len(tow_infeas),
                    margins=margins_tow),
        AWAY=dict(n=len(awy), sib_feasible=len(awy_feas),
                  sib_infeasible=len(awy) - len(awy_feas), margins=margins_awy),
        note=("margin = winner_score − sib_score (bonus potrzebny do flipa). "
              "TOWARD chcemy flipnąć (bonus≥margin); AWAY chcemy NIE flipnąć (bonus<margin). "
              "sib_infeasible = bonus nie pomoże (poza feasible pool)."),
    )
    print(json.dumps(dict(summary=summary, rows=out), indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
