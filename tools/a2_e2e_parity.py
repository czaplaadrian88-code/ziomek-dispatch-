#!/usr/bin/env python3
"""A2 E2E PARYTET DECYZJI — flaga ENABLE_ORTOOLS_DET_TIME_LIMIT OFF vs ON.

Uzupełnia perf_ortools_det_parity.py (który dowodzi parytetu na WARSTWIE SOLVERA,
woła solve_tsp_with_constraints wprost) o parytet END-TO-END przez PEŁNĄ fasadę
`dispatch_v2.core.decide.decide()` na REALNYCH zdarzeniach NEW_ORDER z events.db,
serializowany REALNYM shadow_dispatcher._serialize_result (jak w perf_lazy_harness
tryb parity). READ-ONLY: nie tyka flags.json (klucza A2 tam nie ma → wystarczy
ustawić stałą modułu common), nie pisze do dispatch_state (writer last-known-pos
zablokowany no-opem), zapisuje wyłącznie własne artefakty w eod_drafts_a2/.

3 przebiegi na TYM SAMYM zbiorze case'ów (zbudowanym RAZ):
  A  = A2 OFF          (common.ENABLE_ORTOOLS_DET_TIME_LIMIT=False)
  A' = A2 OFF ponownie (KONTROLA determinizmu — powinno = A)
  B  = A2 ON           (True, ORTOOLS_DET_WALL_CEILING_MS=0)

Diff per-case: A vs A' (kontrola / szum wall-clock) i A vs B (test A2). Różnica
MATERIALNA = inny wybrany kurier (best.courier_id) / inna kolejność trasy
(best.plan.sequence) / inny werdykt (verdict/auto_route) / best None↔obecny /
zmiana rankingu kandydatów. Reszta (score, ETA hhmm, km, min) = pochodna.

Import: pkgroot z symlinkiem dispatch_v2 -> wt-perf-p95 + ZIOMEK_SCRIPTS_ROOT
(gotcha C12g: bez tego -m z cwd=canon cieniuje worktree). SELF-CHECK po imporcie.
"""
from __future__ import annotations

import argparse
import copy
import json
import os
import sys
import time
from pathlib import Path

WT = Path("/root/.openclaw/workspace/scripts/wt-perf-p95")
SCRATCH = Path(os.environ.get(
    "A2_SCRATCH",
    "/tmp/claude-0/-root/2912fb4d-119f-49ae-8211-48bc0ea6976c/scratchpad"))
PKGROOT = SCRATCH / "a2_pkgroot"
OUTDIR = WT / "eod_drafts_a2"


def _ensure_hashseed():
    """PYTHONHASHSEED musi być ustawiony PRZED startem interpretera → re-exec."""
    if os.environ.get("PYTHONHASHSEED") != "0":
        os.environ["PYTHONHASHSEED"] = "0"
        os.execv(sys.executable, [sys.executable] + sys.argv)


def _setup_pkgroot():
    PKGROOT.mkdir(parents=True, exist_ok=True)
    link = PKGROOT / "dispatch_v2"
    if link.is_symlink() or link.exists():
        try:
            link.unlink()
        except OSError:
            pass
    link.symlink_to(WT)
    os.environ["ZIOMEK_SCRIPTS_ROOT"] = str(PKGROOT)


_ensure_hashseed()
_setup_pkgroot()
sys.path.insert(0, str(WT / "tools"))

import perf_lazy_harness as H          # noqa: E402  (ustawia sys.path na pkgroot)
from dispatch_v2 import common as C     # noqa: E402
from dispatch_v2 import courier_resolver as CR  # noqa: E402
from dispatch_v2 import tsp_solver as T  # noqa: E402

SD = H.SD  # realny serializer

# belt-and-suspenders: żaden przebieg nie pisze do produkcyjnego store pozycji.
# Licznik = dowód, ile razy ścieżka zapisu BYŁABY tknięta (zdywertowana no-opem).
_WRITE_ATTEMPTS = {"save_last_known_pos": 0}


def _blocked_save(*a, **k):
    _WRITE_ATTEMPTS["save_last_known_pos"] += 1
    return None


CR._save_last_known_pos = _blocked_save


# ─────────────────────────── self-check importu ───────────────────────────

def self_check() -> dict:
    real = os.path.realpath(C.__file__)
    return {
        "has_flag_attr": hasattr(C, "ENABLE_ORTOOLS_DET_TIME_LIMIT"),
        "common_file": C.__file__,
        "common_realpath": real,
        "points_to_worktree": "wt-perf-p95" in real,
        "flags_json_has_a2_key": "ENABLE_ORTOOLS_DET_TIME_LIMIT" in (C.load_flags() or {}),
    }


def flag_wiring_probe() -> dict:
    """Dowód, że flaga ZMIENIA budżet solvera W TYM procesie (nie no-op)."""
    C.ENABLE_ORTOOLS_DET_TIME_LIMIT = False
    off = T._ortools_det_budget()
    C.ENABLE_ORTOOLS_DET_TIME_LIMIT = True
    C.ORTOOLS_DET_WALL_CEILING_MS = 0
    on = T._ortools_det_budget()
    C.ENABLE_ORTOOLS_DET_TIME_LIMIT = False
    return {"off_budget": off, "on_budget": on, "flag_changes_budget": off != on}


# ─────────────────────────── ekstrakcja pól ───────────────────────────

def key_fields(d: dict) -> dict:
    """Pola MATERIALNE decyzji (wybór kuriera / trasa / werdykt / ranking)."""
    if not isinstance(d, dict):
        return {"__nondict__": repr(d)}
    if "__ERR__" in d:
        return {"__ERR__": d["__ERR__"]}
    best = d.get("best")
    chosen = best.get("courier_id") if isinstance(best, dict) else None
    plan = best.get("plan") if isinstance(best, dict) else None
    seq = plan.get("sequence") if isinstance(plan, dict) else None
    strat = plan.get("strategy") if isinstance(plan, dict) else None
    alts = d.get("alternatives") or []
    ranking = ([chosen] if chosen is not None else []) + [
        a.get("courier_id") for a in alts if isinstance(a, dict)]
    return {
        "verdict": d.get("verdict"),
        "auto_route": d.get("auto_route"),
        "chosen": chosen,
        "best_present": best is not None,
        "route_seq": json.dumps(seq, default=str) if seq is not None else None,
        "strategy": strat,
        "ranking": json.dumps(ranking, default=str),
    }


# pola PIERWSZORZĘDNIE materialne (flip = zmiana samej decyzji, nie tylko rankingu)
_PRIMARY = ("verdict", "auto_route", "chosen", "best_present", "route_seq")


def primary_diff(ka: dict, kb: dict) -> list:
    return [k for k in _PRIMARY if ka.get(k) != kb.get(k)]


def material_diff(ka: dict, kb: dict) -> list:
    keys = set(ka) | set(kb)
    return sorted(k for k in keys if ka.get(k) != kb.get(k))


def flat(prefix, x, out):
    if isinstance(x, dict):
        for k, v in x.items():
            flat(f"{prefix}.{k}" if prefix else k, v, out)
    elif isinstance(x, list):
        out[prefix] = json.dumps(x, sort_keys=True, default=str)
    else:
        out[prefix] = x
    return out


def full_diff_paths(da: dict, db: dict) -> list:
    fa, fb = flat("", da, {}), flat("", db, {})
    keys = set(fa) | set(fb)
    return sorted(k for k in keys if fa.get(k) != fb.get(k))


# ─────────────────────────── przebieg ───────────────────────────

def run_pass(cases, flag_on: bool, jsonl_path: Path):
    C.ENABLE_ORTOOLS_DET_TIME_LIMIT = bool(flag_on)
    C.ORTOOLS_DET_WALL_CEILING_MS = 0
    results = []
    with open(jsonl_path, "w") as fh:
        for i, (oe, now, fs, fl) in enumerate(cases):
            oe2 = copy.deepcopy(oe)
            fl2 = copy.deepcopy(fl)
            oid = oe.get("order_id")
            try:
                res = H._decide(H.WorldState(fleet_snapshot=fl2, now=now), oe2)
                d = H._strip(SD._serialize_result(res, "PARITY", 0.0))
            except Exception as e:  # noqa: BLE001
                d = {"__ERR__": repr(e)}
            results.append((i, fs, oid, d))
            fh.write(f"{i}\t{fs}\t{oid}\t"
                     + json.dumps(d, ensure_ascii=False, sort_keys=True, default=str)
                     + "\n")
    return results


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=800,
                    help="limit NEW_ORDER z events.db (usable ≈ 97%)")
    ap.add_argument("--seed", type=int, default=7)
    a = ap.parse_args()

    OUTDIR.mkdir(parents=True, exist_ok=True)
    sc = self_check()
    probe = flag_wiring_probe()
    if not (sc["has_flag_attr"] and sc["points_to_worktree"]):
        print("SELF-CHECK FAIL — import NIE z worktree:", json.dumps(sc, indent=2))
        return 2

    # zbiór case'ów RAZ (fleet cycling 0/3/5/8/10/12 wewnątrz _cases(None))
    t0 = time.perf_counter()
    cases = H._cases(a.n, a.seed, None)
    build_s = time.perf_counter() - t0
    # sanity deepcopy
    try:
        copy.deepcopy(cases[0][3])
        deepcopy_ok = True
    except Exception as e:  # noqa: BLE001
        deepcopy_ok = False
        print("UWAGA deepcopy floty nieudany:", repr(e))

    n = len(cases)
    fleet_hist = {}
    for _, fs, _, _ in [(c[0], c[2], None, None) for c in cases]:
        fleet_hist[fs] = fleet_hist.get(fs, 0) + 1

    print(f"[a2] self_check={sc}")
    print(f"[a2] flag_probe={probe}")
    print(f"[a2] n_cases={n} build_s={build_s:.1f} deepcopy_ok={deepcopy_ok} "
          f"fleet_hist={fleet_hist}")

    tA = time.perf_counter()
    A = run_pass(cases, False, OUTDIR / "a2_pass_A_off.jsonl")
    print(f"[a2] pass A (OFF) done {time.perf_counter()-tA:.1f}s")
    tAp = time.perf_counter()
    Ap = run_pass(cases, False, OUTDIR / "a2_pass_Aprime_off.jsonl")
    print(f"[a2] pass A' (OFF ctrl) done {time.perf_counter()-tAp:.1f}s")
    tB = time.perf_counter()
    B = run_pass(cases, True, OUTDIR / "a2_pass_B_on.jsonl")
    print(f"[a2] pass B (ON) done {time.perf_counter()-tB:.1f}s")

    # ─────────── diff ───────────
    control_full = 0
    control_material = 0
    control_primary_cases = set()
    offon_full = 0
    offon_material = 0
    offon_primary = 0
    red_cases = []       # primary diff OFF↔ON na case'ach z KONTROLĄ CZYSTĄ (primary)
    noise_cases = []     # primary diff OFF↔ON ale kontrola też primary-różna
    material_examples = []
    field_counter = {}       # licznik różniących się pól OFF↔ON (materialne / key_fields)
    full_field_counter = {}  # licznik różniących się LIŚCI całej serializacji OFF↔ON
    control_full_field_counter = {}  # licznik różniących się LIŚCI OFF↔OFF (ambient)
    full_diff_examples = []   # próbki case'ów z NIEmaterialną różnicą bajtową OFF↔ON

    for idx in range(n):
        _, fs, oid, dA = A[idx]
        _, _, _, dAp = Ap[idx]
        _, _, _, dB = B[idx]
        kA, kAp, kB = key_fields(dA), key_fields(dAp), key_fields(dB)

        # kontrola
        if dA != dAp:
            control_full += 1
            for p in full_diff_paths(dA, dAp):
                control_full_field_counter[p] = control_full_field_counter.get(p, 0) + 1
        cprim = primary_diff(kA, kAp)
        if kA != kAp:
            control_material += 1
        if cprim:
            control_primary_cases.add(idx)

        # test OFF↔ON
        if dA != dB:
            offon_full += 1
            fpaths = full_diff_paths(dA, dB)
            for p in fpaths:
                full_field_counter[p] = full_field_counter.get(p, 0) + 1
            if not primary_diff(kA, kB) and len(full_diff_examples) < 25:
                full_diff_examples.append({
                    "idx": idx, "order_id": oid, "fleet_size": fs,
                    "control_clean": (dA == dAp),
                    "diff_paths": fpaths[:25],
                })
        mdiff = material_diff(kA, kB)
        if mdiff:
            offon_material += 1
            for k in mdiff:
                field_counter[k] = field_counter.get(k, 0) + 1
        pdiff = primary_diff(kA, kB)
        if pdiff:
            offon_primary += 1
            rec = {
                "idx": idx, "order_id": oid, "fleet_size": fs,
                "primary_fields": pdiff,
                "OFF": {k: kA.get(k) for k in pdiff},
                "ON": {k: kB.get(k) for k in pdiff},
                "control_primary_also_differs": idx in control_primary_cases,
            }
            if idx in control_primary_cases:
                noise_cases.append(rec)
            else:
                red_cases.append(rec)
            if len(material_examples) < 40:
                material_examples.append(rec)

    material_parity_pct = round(100 * (n - offon_primary) / max(1, n), 3)
    byte_parity_pct = round(100 * (n - offon_full) / max(1, n), 3)
    # pola różniące się OFF↔ON, których KONTROLA (OFF↔OFF) nigdy nie rusza =
    # deterministycznie przypisywalne A2 (a nie ambient live-state noise).
    a2_attributable_fields = sorted(
        set(full_field_counter) - set(control_full_field_counter))

    # werdykt (materialne = wybór kuriera / kolejność trasy / werdykt / ranking)
    if red_cases:
        verdict = (f"RED ({len(red_cases)} materialnych różnic decyzji OFF↔ON przy "
                   "CZYSTEJ kontroli — flip przypisany A2)")
    elif offon_full == 0 and control_full == 0:
        verdict = "GREEN (bajt-parytet OFF↔ON, kontrola=0)"
    elif offon_primary == 0 and offon_material == 0:
        # zero różnic materialnych; pozostają różnice pochodne (score/ETA/ekonomika)
        if not a2_attributable_fields:
            verdict = ("GREEN-Z-SZUMEM (0 różnic materialnych decyzji OFF↔ON; "
                       "różnice bajtowe WYŁĄCZNIE w polach, które migoczą też w "
                       "kontroli OFF↔OFF = ambient live-state/wall-clock, nie A2)")
        else:
            verdict = ("GREEN-Z-SZUMEM (0 różnic materialnych decyzji OFF↔ON; A2 "
                       "deterministycznie przesuwa tylko pola POCHODNE: "
                       f"{a2_attributable_fields} — nie zmienia wyboru/trasy/werdyktu)")
    else:
        verdict = ("GREEN-Z-SZUMEM (różnice materialne OFF↔ON tylko na case'ach z "
                   "niECZYSTĄ kontrolą = szum, nie deterministyczny efekt A2)")

    summary = {
        "self_check": sc,
        "flag_wiring_probe": probe,
        "n_cases": n,
        "fleet_hist": fleet_hist,
        "deepcopy_ok": deepcopy_ok,
        "control_full_diffs_A_vs_Aprime": control_full,
        "control_material_diffs_A_vs_Aprime": control_material,
        "control_primary_diff_cases": len(control_primary_cases),
        "offon_full_diffs_A_vs_B": offon_full,
        "offon_material_diffs_A_vs_B": offon_material,
        "offon_primary_diffs_A_vs_B": offon_primary,
        "red_cases_n": len(red_cases),
        "noise_cases_n": len(noise_cases),
        "material_parity_pct": material_parity_pct,
        "byte_parity_pct": byte_parity_pct,
        "offon_material_field_counter": field_counter,
        "offon_full_field_counter": full_field_counter,
        "control_full_field_counter": control_full_field_counter,
        "a2_attributable_full_fields": a2_attributable_fields,
        "diverted_store_write_attempts": dict(_WRITE_ATTEMPTS),
        "verdict": verdict,
    }
    with open(OUTDIR / "a2_e2e_parity_summary.json", "w") as fh:
        json.dump({"summary": summary,
                   "red_cases": red_cases,
                   "noise_cases": noise_cases[:40],
                   "material_examples": material_examples,
                   "full_diff_examples": full_diff_examples}, fh,
                  indent=2, ensure_ascii=False, default=str)

    _write_report(summary, red_cases, noise_cases, material_examples,
                  full_diff_examples)
    print("\n=== SUMMARY ===")
    print(json.dumps(summary, indent=2, ensure_ascii=False, default=str))
    return 0


def _write_report(s, red, noise, examples, full_ex=None):
    L = []
    L.append("# A2 E2E PARYTET DECYZJI — ENABLE_ORTOOLS_DET_TIME_LIMIT OFF vs ON")
    L.append("")
    L.append(f"Data: {time.strftime('%Y-%m-%d %H:%M:%S %Z')}")
    L.append("Ścieżka decyzji: dispatch_v2.core.decide.decide() (pełna fasada) na "
             "REALNYCH NEW_ORDER z events.db; serializacja realnym "
             "shadow_dispatcher._serialize_result; _strip usuwa pola czysto-czasowe.")
    L.append("READ-ONLY: flags.json nietknięty (klucz A2 tam nieobecny → stała "
             "common steruje), zero zapisu do dispatch_state (writer last-pos no-op).")
    L.append("")
    L.append("## SELF-CHECK importu")
    for k, v in s["self_check"].items():
        L.append(f"- {k}: `{v}`")
    L.append(f"- flag_wiring_probe (budżet solvera): `{s['flag_wiring_probe']}`")
    L.append("")
    L.append("## Wynik")
    L.append(f"- n_cases: **{s['n_cases']}**  (rozkład floty: {s['fleet_hist']})")
    L.append(f"- KONTROLA A vs A' (OFF↔OFF): bajt-różnic **{s['control_full_diffs_A_vs_Aprime']}**, "
             f"materialnych **{s['control_material_diffs_A_vs_Aprime']}**, "
             f"primary-różnych case'ów **{s['control_primary_diff_cases']}**")
    L.append(f"- TEST A vs B (OFF↔ON): bajt-różnic **{s['offon_full_diffs_A_vs_B']}**, "
             f"materialnych **{s['offon_material_diffs_A_vs_B']}**, "
             f"primary (wybór/trasa/werdykt) **{s['offon_primary_diffs_A_vs_B']}**")
    L.append(f"- parytet MATERIALNY (primary): **{s['material_parity_pct']}%**")
    L.append(f"- parytet BAJTOWY (cała serializacja): **{s['byte_parity_pct']}%**")
    L.append(f"- RED case'ów (primary flip przy czystej kontroli): **{s['red_cases_n']}**")
    L.append(f"- NOISE case'ów (primary flip, kontrola też migoce): **{s['noise_cases_n']}**")
    L.append(f"- writer last-known-pos zdywertowany no-opem, prób zapisu MOJEGO procesu: "
             f"**{s['diverted_store_write_attempts']}** (0 = zero zapisu do dispatch_state)")
    if s["offon_material_field_counter"]:
        L.append(f"- pola różniące się OFF↔ON (materialne key_fields): `{s['offon_material_field_counter']}`")
    if s.get("offon_full_field_counter"):
        L.append(f"- LIŚCIE serializacji różniące się OFF↔ON (pochodne, niematerialne): "
                 f"`{s['offon_full_field_counter']}`")
    if s.get("control_full_field_counter"):
        L.append(f"- LIŚCIE różniące się w KONTROLI OFF↔OFF (ambient live-state noise): "
                 f"`{s['control_full_field_counter']}`")
    L.append(f"- pola OFF↔ON, których KONTROLA nigdy nie rusza = przypisywalne A2: "
             f"**`{s['a2_attributable_full_fields']}`** "
             f"(puste = A2 nie wprowadza żadnej dywergencji ponad ambient)")
    L.append("")
    if full_ex:
        L.append("## Niematerialne różnice bajtowe OFF↔ON (charakterystyka)")
        for r in full_ex[:15]:
            L.append(f"- idx={r['idx']} order_id={r['order_id']} fleet={r['fleet_size']} "
                     f"kontrola_czysta={r['control_clean']} paths={r['diff_paths']}")
        L.append("")
    L.append("## WERDYKT")
    L.append(f"**{s['verdict']}**")
    L.append("")
    if red:
        L.append("## RED — przykłady (primary flip, kontrola czysta)")
        for r in red[:20]:
            L.append(f"- idx={r['idx']} order_id={r['order_id']} fleet={r['fleet_size']} "
                     f"pola={r['primary_fields']} OFF={r['OFF']} ON={r['ON']}")
        L.append("")
    if noise:
        L.append("## NOISE — przykłady (primary flip, ale kontrola też różna = szum wall-clock)")
        for r in noise[:20]:
            L.append(f"- idx={r['idx']} order_id={r['order_id']} fleet={r['fleet_size']} "
                     f"pola={r['primary_fields']} OFF={r['OFF']} ON={r['ON']}")
        L.append("")
    L.append("## Artefakty")
    L.append("- surowe JSONL: `a2_pass_A_off.jsonl`, `a2_pass_Aprime_off.jsonl`, `a2_pass_B_on.jsonl`")
    L.append("- pełne dane: `a2_e2e_parity_summary.json`")
    with open(OUTDIR / "a2_e2e_parity_report.md", "w") as fh:
        fh.write("\n".join(L) + "\n")


if __name__ == "__main__":
    sys.exit(main())
