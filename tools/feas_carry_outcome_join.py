#!/usr/bin/env python3
"""feas_carry_outcome_join — L7.4 (root R3-E1, 2026-07-03). READ-ONLY.

Jedyny join-harness dla mechanizmu B2 feas-carry-readmit (#483000, flaga
`ENABLE_FEAS_CARRY_READMIT`, LIVE 2026-06-27 22:18 → OFF; PRZED ewentualnym
re-flipem trzeba orzec: czy REALNE re-admity były trafne na FIZYCZNYCH czasach,
nie na projekcji). Zastępuje 3 VOID przyrządy (feas_carry_blind_review /
feas_carry_readmit_replay / feas_carry_readmit_postflip), które wydają werdykt
na PROJEKTOWANYM `regret_min` bez joinu z dostawą.

Dlaczego istniejące toole były VOID: `regret_min = chosen_objm − rej_objm`, gdzie
objm = `objm_r6_breach_max_min` = MODELOWY (decyzyjny) najgorszy per-order R6 breach
w minutach. To projekcja z chwili decyzji — nikt nie sprawdził fizycznie, czy
re-admit realnie dostarczył na czas ani czy „wybaczony" breach chosen'a faktycznie
zaszkodził. Ten harness dokłada BRAKUJĄCĄ nogę: join po order_id z fizyczną prawdą.

ŹRÓDŁA (przez kanon ledger_io — rotation-aware, jedno źródło):
  - REALNE re-admity: shadow_decisions ledger, rekord z best.feas_carry_readmit==True
    (durable, przeżywa retencję journalda — 4 fires 28.06 to jedyne LIVE re-admity).
  - Populacja shadow „would_redirect": dispatch_state/feas_carry_blind_shadow.jsonl
    (co decyzja chosen niósł wybaczony breach; co często warto by przekierować).
  - OUTCOME: decision_outcomes (proxy „przycisk", ~98% okna) + gps_delivery_truth
    (fizyka GPS, ~11% okna) — ledger_io.load_outcomes / load_gps_truth.

RE-SPEC ETYKIETY REGRET (opis w raporcie L7.4):
  STARY (projekcja):  regret_min = chosen_objm − rej_objm  [oczekiwana redukcja
                      modelowego worst-breach; NIGDY nie zweryfikowana fizycznie].
  NOWY (outcome):     realized_forgiveness_cost_min = max(0, physical_r6 − 35)
                      dla zlecenia na ŚCIEŻCE FAKTYCZNIE WZIĘTEJ (chosen gdy re-admit
                      nie wykonany; target re-admita gdy wykonany), join po order_id
                      do telemetrii dostawy. To fizycznie ZAOBSERWOWANY breach (lub
                      jego brak), nie projekcja. Sygnatura: >0 = wybaczenie realnie
                      kosztowało breach (redirect by pomógł); 0 = wybaczenie nieszkodliwe
                      (redirect = churn bez fizycznego zysku).

Użycie:
  cd /root/.openclaw/workspace/scripts
  PYTHONPATH=. /root/.openclaw/venvs/dispatch/bin/python \
      dispatch_v2/tools/feas_carry_outcome_join.py --days 14 [--out PATH] [--selftest]

NIE hot-path. Nie dotyka decyzji/stanu. Wyjście: tabela (stdout) + JSONL per-wiersz.
"""
import argparse
import json
import os
import sys
from datetime import datetime, timedelta, timezone

# kanon ledger_io / rotated_logs (rotation-aware readers, jedno źródło)
try:
    from dispatch_v2.tools import ledger_io
except ImportError:
    _here = os.path.dirname(os.path.abspath(__file__))
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(_here))))
    from dispatch_v2.tools import ledger_io

BLIND_SHADOW = "/root/.openclaw/workspace/dispatch_state/feas_carry_blind_shadow.jsonl"
DEFAULT_OUT = "/root/.openclaw/workspace/dispatch_state/feas_carry_outcome_join.jsonl"
SLA_MIN = 35.0  # R6 hard próg (R-35MIN-MAX), ta sama stała co reszta Ziomka


# ─────────────────────────── helpers (czyste, testowalne) ──────────────────────────
def _parse_iso(s):
    """ISO-8601 aware/naive → aware UTC (feas_carry ts = ISO +00:00)."""
    if not s or not isinstance(s, str):
        return None
    t = s.strip().replace("Z", "+00:00") if s.strip().endswith("Z") else s.strip()
    try:
        dt = datetime.fromisoformat(t)
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _num(v):
    return v if isinstance(v, (int, float)) and not isinstance(v, bool) else None


def _cid(v):
    return None if v is None else str(v).strip()


def realized_forgiveness_cost_min(r6_actual, sla_min=SLA_MIN):
    """NOWA etykieta regret (outcome): fizyczny breach na wziętej ścieżce.

    max(0, physical_r6 − 35). >0 = wybaczenie breach chosen'a realnie kosztowało;
    0 = nieszkodliwe. To FIZYCZNIE zaobserwowany koszt, nie projekcja modelu.
    Zwraca None gdy brak fizycznego czasu (brak prawdy → NIE zero-jako-zgoda).
    """
    v = _num(r6_actual)
    if v is None:
        return None
    return round(max(0.0, v - sla_min), 1)


def _pct(n, d):
    return round(100.0 * n / d, 1) if d else 0.0


def _stats(vals):
    v = sorted(x for x in vals if isinstance(x, (int, float)) and not isinstance(x, bool))
    if not v:
        return {"n": 0, "mean": None, "med": None, "p90": None}
    n = len(v)
    return {"n": n, "mean": round(sum(v) / n, 1), "med": v[n // 2],
            "p90": v[min(n - 1, int(n * 0.9))]}


def _outcome_fields(proxy, physical):
    """Wyciąg fizycznego outcome z proxy(przycisk)/physical(GPS). physical > proxy
    dla r6, ale gps_truth nie liczy r6 — proxy niesie r6_actual_min. Zwraca dict."""
    o = {"actual_cid": None, "r6_actual_min": None, "r6_breach": None,
         "delivered_at": None, "verdict": None, "action": None,
         "proposed_cid": None, "truth_source": ledger_io.TRUTH_NONE,
         "confidence": None}
    if proxy:
        o.update(actual_cid=_cid(proxy.get("actual_cid")),
                 r6_actual_min=_num(proxy.get("r6_actual_min")),
                 r6_breach=proxy.get("r6_breach"),
                 delivered_at=proxy.get("delivered_at"),
                 verdict=proxy.get("verdict"), action=proxy.get("action"),
                 proposed_cid=_cid(proxy.get("proposed_cid")))
        o["truth_source"] = ledger_io.TRUTH_PROXY
    if physical:
        o["truth_source"] = ledger_io.TRUTH_PHYSICAL
        o["confidence"] = physical.get("confidence")
        if o["delivered_at"] is None:
            o["delivered_at"] = physical.get("physical_delivered_at")
        if o["actual_cid"] is None:
            o["actual_cid"] = _cid(physical.get("courier_id"))
    return o


# ─────────────────────────── ładowanie źródeł ──────────────────────────
def _load_real_readmits(cutoff):
    """Autorytatywne REALNE re-admity z shadow_decisions ledger (best.feas_carry_readmit).

    Durable — przeżywa retencję journalda. Zwraca listę dict {oid, ts, best_cid,
    from_cid, proj_regret_min, proj_newbag_min, orig_reason}."""
    out = []
    for rec in ledger_io.iter_shadow_decisions(cutoff):
        best = rec.get("best") or {}
        if not best.get("feas_carry_readmit"):
            continue
        out.append({
            "order_id": str(rec.get("order_id")),
            "ts": rec.get("ts"),
            "best_cid": _cid(best.get("courier_id")),
            "from_cid": _cid(best.get("feas_carry_redirect_from_cid")),
            "proj_regret_min": _num(best.get("feas_carry_regret_min")),
            "proj_newbag_min": _num(best.get("feas_carry_newbag_min")),
            "orig_reason": best.get("feas_carry_orig_reason"),
        })
    return out


def _load_blind_shadow(cutoff):
    """Populacja shadow (feas_carry_blind_shadow.jsonl, nierotowany). Zwraca listę
    surowych rekordów z okna [cutoff, teraz] po polu ts."""
    out = []
    if not os.path.exists(BLIND_SHADOW):
        return out
    with open(BLIND_SHADOW, encoding="utf-8", errors="replace") as fh:
        for ln in fh:
            ln = ln.strip()
            if not ln:
                continue
            try:
                r = json.loads(ln)
            except (json.JSONDecodeError, ValueError):
                continue
            if cutoff is not None:
                ts = _parse_iso(r.get("ts"))
                if ts is None or ts < cutoff:
                    continue
            out.append(r)
    return out


# ─────────────────────────── join core ──────────────────────────
def build_rows(cutoff):
    """Zwraca (real_rows, wr_rows, meta). Prawda ładowana bez cutoff — rekord
    prawdy powstaje PO decyzji (join po oid niezależny od czasu)."""
    outcomes = ledger_io.load_outcomes(None)
    gps = ledger_io.load_gps_truth(None)

    # 1) REALNE re-admity (autorytatywne z ledgera)
    real_rows = []
    for d in _load_real_readmits(cutoff):
        oid = d["order_id"]
        proxy, physical = outcomes.get(oid), gps.get(oid)
        oc = _outcome_fields(proxy, physical)
        target = d["best_cid"]  # kurier RE-ADMITOWANY (proponowany po redirect)
        executed = bool(target and oc["actual_cid"] and target == oc["actual_cid"])
        cost = realized_forgiveness_cost_min(oc["r6_actual_min"])
        real_rows.append({
            "kind": "real_readmit", "order_id": oid, "ts": d["ts"],
            "redirect_from_cid": d["from_cid"], "redirect_to_cid": target,
            "proj_regret_min": d["proj_regret_min"], "proj_newbag_min": d["proj_newbag_min"],
            "actual_delivered_cid": oc["actual_cid"], "readmit_executed": executed,
            "verdict": oc["verdict"], "action": oc["action"],
            "phys_r6_actual_min": oc["r6_actual_min"], "phys_r6_breach": oc["r6_breach"],
            "realized_forgiveness_cost_min": cost,
            "truth_source": oc["truth_source"], "truth_confidence": oc["confidence"],
            "delivered_at": oc["delivered_at"],
        })

    # 2) Populacja shadow would_redirect (kontrfaktyk: chosen zachowany)
    wr_rows = []
    for r in _load_blind_shadow(cutoff):
        if not r.get("would_redirect"):
            continue
        oid = str(r.get("order_id"))
        proxy, physical = outcomes.get(oid), gps.get(oid)
        oc = _outcome_fields(proxy, physical)
        cost = realized_forgiveness_cost_min(oc["r6_actual_min"])
        wr_rows.append({
            "kind": "would_redirect", "order_id": oid, "ts": r.get("ts"),
            "chosen_cid": _cid(r.get("chosen_cid")), "redirect_cid": _cid(r.get("redirect_cid")),
            "chosen_forgiven_breach": _num(r.get("chosen_forgiven_breach")),
            "redirect_objm": _num(r.get("redirect_objm")),
            "proj_regret_min": _num(r.get("regret_min")),
            "redirect_kind": r.get("redirect_kind"), "redirect_over_by": _num(r.get("redirect_over_by")),
            "actual_delivered_cid": oc["actual_cid"], "verdict": oc["verdict"],
            "phys_r6_actual_min": oc["r6_actual_min"], "phys_r6_breach": oc["r6_breach"],
            "realized_forgiveness_cost_min": cost,
            "truth_source": oc["truth_source"], "truth_confidence": oc["confidence"],
        })

    n_phys = sum(1 for r in real_rows + wr_rows if r["truth_source"] == ledger_io.TRUTH_PHYSICAL)
    n_proxy = sum(1 for r in real_rows + wr_rows if r["truth_source"] == ledger_io.TRUTH_PROXY)
    meta = {"n_real": len(real_rows), "n_wr": len(wr_rows), "n_phys": n_phys, "n_proxy": n_proxy}
    return real_rows, wr_rows, meta


# ─────────────────────────── raport ──────────────────────────
def _print_report(real_rows, wr_rows, meta, cutoff, out_path):
    print(f"=== feas_carry_outcome_join (L7.4) | okno od {cutoff.date()} | "
          f"prawda: fiz {meta['n_phys']} / proxy {meta['n_proxy']} ===")
    verdict_label = ledger_io.label_verdict(meta["n_phys"], meta["n_proxy"])
    print(f"jakość werdyktu (ledger_io): {verdict_label}\n")

    # --- A. REALNE re-admity (flaga ON window) ---
    print("=== A. REALNE RE-ADMITY (ledger feas_carry_readmit=True; autorytatywne) ===")
    n = len(real_rows)
    joined = [r for r in real_rows if r["truth_source"] != ledger_io.TRUTH_NONE]
    executed = [r for r in real_rows if r["readmit_executed"]]
    overridden = [r for r in real_rows if r["verdict"] == "override" or (not r["readmit_executed"])]
    print(f"  realnych re-admitów w oknie: {n}  | z outcome: {len(joined)}")
    print(f"  WYKONANYCH (kurier re-admitowany faktycznie dowiózł): {len(executed)}")
    print(f"  nadpisanych/nie-wykonanych (koordynator wziął innego):  {len(overridden)}")
    if n:
        br = [r for r in joined if r["phys_r6_breach"] is True]
        ok = [r for r in joined if r["phys_r6_breach"] is False]
        print(f"  fizyczny R6-breach na tych zleceniach: breach {len(br)} / on-time {len(ok)}")
        print("  per-re-admit:")
        for r in real_rows:
            print(f"    oid={r['order_id']} {r['redirect_from_cid']}→{r['redirect_to_cid']} "
                  f"proj_regret={r['proj_regret_min']} proj_newbag={r['proj_newbag_min']} | "
                  f"dowiózł={r['actual_delivered_cid']} exec={r['readmit_executed']} "
                  f"verdict={r['verdict']} | fiz_r6={r['phys_r6_actual_min']} "
                  f"breach={r['phys_r6_breach']} realized_cost={r['realized_forgiveness_cost_min']}")

    # --- B. populacja would_redirect (kontrfaktyk: chosen zachowany) ---
    print("\n=== B. WOULD_REDIRECT (shadow; realized regret wybaczenia na wziętej ścieżce) ===")
    wr_join = [r for r in wr_rows if r["phys_r6_actual_min"] is not None]
    br = [r for r in wr_join if r["phys_r6_breach"] is True]
    costs = [r["realized_forgiveness_cost_min"] for r in wr_join
             if r["realized_forgiveness_cost_min"] is not None]
    harmless = [c for c in costs if c == 0.0]
    print(f"  would_redirect w oknie: {len(wr_rows)}  | z fizycznym r6: {len(wr_join)}")
    print(f"  realized_forgiveness_cost (fizyczny breach chosen'a):")
    st = _stats(costs)
    print(f"    n={st['n']} mean={st['mean']} med={st['med']} p90={st['p90']} min")
    print(f"  wybaczeń NIESZKODLIWYCH (koszt=0, redirect=churn bez zysku): "
          f"{len(harmless)}/{len(costs)} = {_pct(len(harmless), len(costs))}%")
    print(f"  wybaczeń KOSZTOWNYCH (fizyczny breach zaszedł): "
          f"{len(br)}/{len(wr_join)} = {_pct(len(br), len(wr_join))}%")

    # --- WERDYKT ---
    print("\n=== WERDYKT (na FIZYCE, nie projekcji) ===")
    if len(executed) == 0:
        print("  ⚠ REALNE re-admity: 0 wykonanych fizycznie — WSZYSTKIE nadpisane przez")
        print("    koordynatora. Fizycznego dowodu trafności re-admita NIE MA (kurier")
        print("    re-admitowany nie pojechał). Próba za mała na re-flip #483000 z fizyki.")
    else:
        eb = [r for r in executed if r["phys_r6_breach"] is True]
        print(f"  REALNE re-admity wykonane: {len(executed)}, z tego fizyczny breach {len(eb)}.")
    print(f"  Populacja would_redirect: {_pct(len(harmless), max(1, len(costs)))}% wybaczeń "
          f"było fizycznie NIESZKODLIWYCH → tyle redirectów byłoby churnem bez zysku;")
    print(f"    {_pct(len(br), max(1, len(wr_join)))}% chosen'ów fizycznie breachnęło → tam redirect miałby sens.")
    print(f"\n  → zapis per-wiersz: {out_path}")


def _emit(real_rows, wr_rows, out_path):
    tmp = out_path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        for r in real_rows + wr_rows:
            fh.write(json.dumps(r, ensure_ascii=False) + "\n")
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp, out_path)


# ─────────────────────────── oracle self-check ──────────────────────────
def _selftest():
    """Sanity oracle na syntetycznych fixture'ach (bez I/O na żywe dane)."""
    ok = True

    def chk(name, cond):
        nonlocal ok
        ok = ok and cond
        print(f"  [{'OK' if cond else 'FAIL'}] {name}")

    # etykieta regret: fizyczny breach >35 → dodatni koszt; ≤35 → 0; brak → None
    chk("realized_cost breach 47→12.0", realized_forgiveness_cost_min(47.0) == 12.0)
    chk("realized_cost on-time 31.6→0.0", realized_forgiveness_cost_min(31.6) == 0.0)
    chk("realized_cost próg 35→0.0", realized_forgiveness_cost_min(35.0) == 0.0)
    chk("realized_cost brak danych→None", realized_forgiveness_cost_min(None) is None)
    chk("realized_cost bool nie liczbą→None", realized_forgiveness_cost_min(True) is None)
    # _outcome_fields: physical podnosi truth_source; proxy niesie r6
    oc = _outcome_fields({"actual_cid": "520", "r6_actual_min": 31.6, "r6_breach": False,
                          "verdict": "override", "proposed_cid": "492"}, None)
    chk("outcome proxy truth_source", oc["truth_source"] == ledger_io.TRUTH_PROXY)
    chk("outcome proxy r6", oc["r6_actual_min"] == 31.6 and oc["actual_cid"] == "520")
    oc2 = _outcome_fields({"actual_cid": "520", "r6_actual_min": 31.6, "r6_breach": False},
                          {"confidence": "high", "physical_delivered_at": "x", "courier_id": "520"})
    chk("outcome physical > proxy", oc2["truth_source"] == ledger_io.TRUTH_PHYSICAL)
    print(f"\n  self-test {'PASS' if ok else 'FAIL'}")
    return 0 if ok else 1


def main():
    ap = argparse.ArgumentParser(description="feas-carry outcome-join (L7.4, read-only).")
    ap.add_argument("--days", type=int, default=14, help="okno wstecz (dni) wg ts decyzji")
    ap.add_argument("--out", default=DEFAULT_OUT, help="ścieżka JSONL per-wiersz")
    ap.add_argument("--selftest", action="store_true", help="oracle sanity, bez żywych danych")
    args = ap.parse_args()

    if args.selftest:
        return _selftest()

    cutoff = datetime.now(timezone.utc) - timedelta(days=args.days)
    real_rows, wr_rows, meta = build_rows(cutoff)
    _emit(real_rows, wr_rows, args.out)
    _print_report(real_rows, wr_rows, meta, cutoff, args.out)
    print("\n  (read-only; zero wpływu na decyzje/stan)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
