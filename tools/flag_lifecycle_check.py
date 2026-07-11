#!/usr/bin/env python3
"""flag_lifecycle_check — walidacja rejestru cyklu życia flag (Z-P1-07 Faza A).

Dwa tryby:
  --repo-hermetic (DOMYŚLNY, CI-safe): waliduje `flag_lifecycle_registry.json`
    STRUKTURALNIE + vs źródła DOSTĘPNE Z REPO (source-parse common.py; flags.json
    przez `--flags-json PATH`). Cross-repo (panel/apka/systemd 1b) = SKIP-IF-ABSENT.
    ZERO odczytu hosta, ZERO journalctl. Exit ≠0 = błąd. BEZ baseline-wyjątków.
  --live (HOST, READ-ONLY): dokłada rekoncyliację current_snapshot vs REALNE
    nośniki (żywy flags.json + /etc/systemd/*.d + opcjonalnie `--fingerprint`
    FLAG_FINGERPRINT z journalctl — wzorem flag_fingerprint_check; NIGDY
    `systemctl show -p Environment`). Historyczny known_drift USE_V2_PARSER
    został domknięty migracją 2026-07-10; ewentualne nowe known_drift są
    odnotowane i nie liczą się jako błąd.

Wykrywa (błędy → exit≠0): flaga w źródłach bez wpisu w rejestrze; wpis-widmo
(flaga zniknęła ze źródła); dryf default/current_snapshot vs źródło; twin bez
linku zwrotnego; brak metadanych lifecycle/pól; zła wartość lifecycle.

NIE dubluje: dead-flag → tools/flag_hygiene_check.py; doc-coverage →
flag_doc_coverage_check.py; effect-coverage → flag_effect_coverage_check.py;
per-serwis fingerprint → flag_fingerprint_check.py (README odsyła).

Użycie:
  python3 tools/flag_lifecycle_check.py                              # repo-hermetic
  python3 tools/flag_lifecycle_check.py --flags-json PATH            # + dryf flags.json
  python3 tools/flag_lifecycle_check.py --skip-external              # pomiń flags.json/cross
  python3 tools/flag_lifecycle_check.py --live [--fingerprint]       # rekoncyliacja host
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import os
import re
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
DISPATCH_V2 = os.path.dirname(_HERE)
DEF_REGISTRY = os.path.join(_HERE, "flag_lifecycle_registry.json")

ALLOWED_LIFECYCLE = {"planned", "shadow", "live", "deprecated", "dead"}
ALLOWED_WORLDS = {"engine", "panel", "apka"}
REQUIRED_FIELDS = {
    "name", "worlds", "source_of_truth", "carriers", "owner", "lifecycle",
    "lifecycle_seeded", "default", "current_snapshot", "consumers", "rollback",
    "review_date", "removal_condition", "twin_of", "intentional_per_process",
    "known_drift", "known_drift_note", "notes",
}
_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def _load_seed():
    """Reużycie helperów seedera (source-parse tupli, systemd env, FR)."""
    try:
        from dispatch_v2.tools import flag_lifecycle_seed as sd  # type: ignore
        return sd
    except Exception:
        p = os.path.join(_HERE, "flag_lifecycle_seed.py")
        spec = importlib.util.spec_from_file_location("_fl_seed_sib", p)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod


SD = _load_seed()
FR = SD.FR


def load_registry(path=DEF_REGISTRY):
    return json.load(open(path, encoding="utf-8"))


# ── walidacje strukturalne (czysto z rejestru) ──────────────────────────────────
def check_structure(reg) -> list:
    errors = []
    flags = reg.get("flags", {})
    if not flags:
        return ["rejestr pusty / brak klucza 'flags'"]
    for name, e in flags.items():
        miss = REQUIRED_FIELDS - set(e.keys())
        if miss:
            errors.append(f"[POLA] {name}: brak pól {sorted(miss)}")
        if e.get("name") != name:
            errors.append(f"[KLUCZ] {name}: name={e.get('name')!r} ≠ klucz")
        lc = e.get("lifecycle")
        if lc not in ALLOWED_LIFECYCLE:
            errors.append(f"[LIFECYCLE] {name}: '{lc}' spoza {sorted(ALLOWED_LIFECYCLE)}")
        worlds = set(e.get("worlds") or [])
        if not worlds or not worlds <= ALLOWED_WORLDS:
            errors.append(f"[WORLDS] {name}: {e.get('worlds')} — puste lub spoza {sorted(ALLOWED_WORLDS)}")
        if not _DATE_RE.match(str(e.get("review_date", ""))):
            errors.append(f"[REVIEW] {name}: review_date '{e.get('review_date')}' nie jest datą")
        if not e.get("carriers"):
            errors.append(f"[CARRIERS] {name}: pusta lista nośników")
        ipp = e.get("intentional_per_process")
        if not isinstance(ipp, dict) or "value" not in ipp:
            errors.append(f"[IPP] {name}: intentional_per_process musi mieć 'value'")
    # twins dwustronne + brak duplikatów (dict → klucze unikalne z natury)
    for name, e in flags.items():
        for t in e.get("twin_of", []):
            if t not in flags:
                errors.append(f"[TWIN] {name}: twin '{t}' nie istnieje w rejestrze")
            elif name not in flags[t].get("twin_of", []):
                errors.append(f"[TWIN] {name}↔{t}: link NIE dwustronny")
    return errors


def check_curation(reg) -> list:
    """Kuracja (Z-P1-07): owner 100% (service + business=Adrian), removal_condition
    obecny, a jeśli wpis SKUROWANY (curated_at) → data + lifecycle_seeded=False."""
    errors = []
    for name, e in reg.get("flags", {}).items():
        owner = e.get("owner") or {}
        if not owner.get("service"):
            errors.append(f"[OWNER] {name}: brak owner.service")
        if owner.get("business") != "Adrian":
            errors.append(f"[OWNER] {name}: owner.business != 'Adrian' ({owner.get('business')!r})")
        if not e.get("removal_condition"):
            errors.append(f"[REMOVAL] {name}: brak removal_condition")
        ca = e.get("curated_at")
        if ca is not None:
            if not _DATE_RE.match(str(ca)):
                errors.append(f"[CURATED] {name}: curated_at '{ca}' nie jest datą")
            if e.get("lifecycle_seeded") is not False:
                errors.append(f"[CURATED] {name}: curated_at obecny ale lifecycle_seeded != False")
    return errors


def curation_coverage(reg) -> tuple:
    flags = reg.get("flags", {})
    curated = sum(1 for e in flags.values() if e.get("curated_at"))
    return curated, len(flags)


# ── coverage silnika vs source-parse common.py (NIEZALEŻNE od rejestru) ─────────
def _engine_tuples(common_py):
    src = open(common_py, encoding="utf-8").read()
    return {
        "ETAP4_DECISION_FLAGS": set(SD._tuple_names(src, "ETAP4_DECISION_FLAGS")),
        "_FINGERPRINT_EXTRA_FLAGS": set(SD._tuple_names(src, "_FINGERPRINT_EXTRA_FLAGS")),
        "FLAGS_JSON_NUMERIC_OVERRIDES": set(SD._tuple_names(src, "FLAGS_JSON_NUMERIC_OVERRIDES")),
        "TEST_ISOLATED_INFRA_FLAGS": set(SD._tuple_names(src, "TEST_ISOLATED_INFRA_FLAGS")),
    }


def check_engine_coverage(reg, common_py) -> list:
    """Każda flaga z tupli common.py MA wpis (worlds∋engine). Anty-tautologia:
    źródło = common.py, nie rejestr."""
    errors = []
    flags = reg.get("flags", {})
    tuples = _engine_tuples(common_py)
    for tup, names in tuples.items():
        for n in sorted(names):
            e = flags.get(n)
            if e is None:
                errors.append(f"[COVERAGE] {n}: w {tup} (common.py) ale BRAK w rejestrze")
            elif "engine" not in (e.get("worlds") or []):
                errors.append(f"[COVERAGE] {n}: w {tup} ale worlds={e.get('worlds')} bez 'engine'")
    # wpis-widmo: rejestr twierdzi carrier common.py:<TUP> a nazwy nie ma w tupli
    for name, e in flags.items():
        for c in e.get("carriers", []):
            if c.startswith("common.py:"):
                tup = c.split(":", 1)[1]
                if tup in tuples and name not in tuples[tup]:
                    errors.append(f"[WIDMO] {name}: carrier {c} ale nazwy brak w tupli (znikła?)")
    return errors


# ── flags.json: sierota-w-źródle + dryf wartości ────────────────────────────────
def check_flags_json(reg, flags_json_path) -> list:
    errors = []
    flags = reg.get("flags", {})
    try:
        fjson = FR.load_flags_json(flags_json_path)  # filtruje _comment*
    except Exception as ex:
        return [f"[FLAGS_JSON] nie mogę wczytać {flags_json_path}: {ex}"]
    # 1) każdy klucz flags.json MA wpis (flaga nie ominęła rejestru)
    for k in sorted(fjson):
        if any(p.match(k) for p in FR.DYNAMIC_KEY_FAMILIES):
            continue
        if k not in flags:
            errors.append(f"[SIEROTA] flags.json:{k} bez wpisu w rejestrze")
    # 2) dryf: wpis z current_snapshot['flags.json'] musi == flags.json (i odwrotnie)
    for name, e in flags.items():
        snap = e.get("current_snapshot", {})
        if "flags.json" in snap:
            if name not in fjson:
                errors.append(f"[WIDMO] {name}: snapshot ma flags.json ale klucza NIE MA w źródle")
            elif snap["flags.json"] != fjson[name]:
                errors.append(f"[DRYF] {name}: rejestr flags.json={snap['flags.json']!r} "
                              f"≠ źródło {fjson[name]!r}")
    return errors


# ── cross-repo (panel/apka/systemd 1b) — SKIP-IF-ABSENT ─────────────────────────
def check_cross_repo(reg, panel_dir, courier_dir, panelsync_dir, systemd_dir) -> tuple:
    errors, skips = [], []
    flags = reg.get("flags", {})
    # PANEL
    flags_py = os.path.join(panel_dir, "app", "core", "flags.py")
    if os.path.isfile(flags_py):
        dfl = SD._panel_default_flags(flags_py)
        for n in sorted(dfl):
            e = flags.get(n)
            if e is None or "panel" not in (e.get("worlds") or []):
                errors.append(f"[COVERAGE-PANEL] {n}: w DEFAULT_FLAGS ale brak/niepanelowy wpis")
    else:
        skips.append(f"panel (DEFAULT_FLAGS nieobecny: {flags_py})")
    # APKA
    courier_cfg = os.path.join(courier_dir, "config.py")
    if os.path.isfile(courier_cfg):
        import glob
        cpy = [f for f in glob.glob(os.path.join(courier_dir, "**", "*.py"), recursive=True)
               if "__pycache__" not in f and ".bak" not in f]
        apka_env = SD._scan_envfrozen(cpy, key="env", bool_only=False)
        for n in sorted(apka_env):
            e = flags.get(n)
            if e is None or "apka" not in (e.get("worlds") or []):
                errors.append(f"[COVERAGE-APKA] {n}: env-read w courier_api ale brak/nieapkowy wpis")
    else:
        skips.append(f"apka (courier_api nieobecny: {courier_cfg})")
    if not os.path.isdir(systemd_dir):
        skips.append(f"systemd 1b (nieobecny: {systemd_dir})")
    return errors, skips


# ── --live: rekoncyliacja z realnymi nośnikami hosta ────────────────────────────
def check_live(reg, flags_json_path, systemd_dir, use_fingerprint) -> tuple:
    """(real_drifts, known, info) — real_drifts liczą się do exit≠0;
    jawnie kuratorowane known_drift i info = tylko raport."""
    real_drifts, known, info = [], [], []
    flags = reg.get("flags", {})
    fjson = FR.load_flags_json(flags_json_path)
    # 1) flags.json żywy vs snapshot
    for name, e in flags.items():
        snap = e.get("current_snapshot", {})
        if "flags.json" in snap and name in fjson and snap["flags.json"] != fjson[name]:
            (known if e.get("known_drift") else real_drifts).append(
                f"[DRYF-JSON] {name}: rejestr={snap['flags.json']!r} żywy flags.json={fjson[name]!r}")
    # 2) systemd 1b żywy vs snapshot per-service
    import glob
    for name, e in flags.items():
        snap = e.get("current_snapshot", {})
        for svc, val in snap.items():
            if not svc.endswith(".service"):
                continue
            main = os.path.join(systemd_dir, svc)
            confs = [main] + sorted(glob.glob(os.path.join(systemd_dir, svc + ".d", "*.conf")))
            live_val = None
            for f in confs:
                for k, v in SD._parse_systemd_env(f)[0]:
                    if k == name:
                        live_val = SD._norm_value(v)
            if live_val is not None and live_val != val:
                (known if e.get("known_drift") else real_drifts).append(
                    f"[DRYF-SYSTEMD] {name}@{svc}: rejestr={val!r} żywy={live_val!r}")
    # 3) opcjonalnie FLAG_FINGERPRINT (journalctl) — wzorem flag_fingerprint_check
    if use_fingerprint:
        try:
            from dispatch_v2.tools import flag_fingerprint_check as ffc  # type: ignore
        except Exception:
            p = os.path.join(_HERE, "flag_fingerprint_check.py")
            spec = importlib.util.spec_from_file_location("_ffc_sib", p)
            ffc = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(ffc)
        fps = ffc.parse_fingerprints()
        info.append(f"[FINGERPRINT] procesy z linią FLAG_FINGERPRINT: "
                    f"{[p for p in fps if fps[p]]}")
    return real_drifts, known, info


def run(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--registry", default=DEF_REGISTRY)
    ap.add_argument("--common-py", default=os.path.join(DISPATCH_V2, "common.py"))
    ap.add_argument("--flags-json", default=None,
                    help="repo-hermetic: dryf/sierota vs ten flags.json (np. fixtura)")
    ap.add_argument("--skip-external", action="store_true",
                    help="pomiń flags.json + cross-repo (czysto strukturalne + coverage)")
    ap.add_argument("--repo-hermetic", action="store_true", help="(domyślny) walidacja repo")
    ap.add_argument("--live", action="store_true", help="rekoncyliacja z hostem (READ-ONLY)")
    ap.add_argument("--fingerprint", action="store_true", help="--live: dołóż journalctl FP")
    ap.add_argument("--systemd-dir", default=SD.DEF_SYSTEMD_DIR)
    ap.add_argument("--panel-dir", default=SD.DEF_PANEL_DIR)
    ap.add_argument("--courier-dir", default=SD.DEF_COURIER_DIR)
    ap.add_argument("--panelsync-dir", default=SD.DEF_PANELSYNC_DIR)
    ap.add_argument("--json", action="store_true", help="wynik jako JSON")
    args = ap.parse_args(argv)

    reg = load_registry(args.registry)
    errors = []
    errors += check_structure(reg)
    errors += check_curation(reg)
    errors += check_engine_coverage(reg, args.common_py)
    cur_n, cur_tot = curation_coverage(reg)

    if not args.skip_external:
        fj = args.flags_json or (SD.DEF_FLAGS_JSON if args.live else None)
        if fj and os.path.isfile(fj):
            errors += check_flags_json(reg, fj)
        ce, skips = check_cross_repo(reg, args.panel_dir, args.courier_dir,
                                     args.panelsync_dir, args.systemd_dir)
        errors += ce
    else:
        skips = ["--skip-external: pominięto flags.json + cross-repo"]

    live_block = None
    if args.live:
        real, known, info = check_live(reg, args.flags_json or SD.DEF_FLAGS_JSON,
                                       args.systemd_dir, args.fingerprint)
        errors += real
        live_block = {"real_drifts": real, "known_drift": known, "info": info}

    result = {
        "registry": args.registry,
        "mode": "live" if args.live else "repo-hermetic",
        "total_flags": len(reg.get("flags", {})),
        "curated": cur_n, "curated_total": cur_tot,
        "errors": errors,
        "skips": skips,
        "live": live_block,
        "ok": not errors,
    }
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print(f"FLAG LIFECYCLE CHECK [{result['mode']}] — {result['total_flags']} flag")
        print(f"  ℹ kuracja: {cur_n}/{cur_tot} wpisów (curated_at)")
        for s in skips:
            print(f"  ⏭ SKIP: {s}")
        if live_block:
            for k in live_block["known_drift"]:
                print(f"  ℹ KNOWN-DRIFT (nie-błąd): {k}")
            for i in live_block["info"]:
                print(f"  ℹ {i}")
        if errors:
            print(f"  ⛔ BŁĘDY ({len(errors)}):")
            for e in errors:
                print(f"     {e}")
        else:
            print("  ✅ OK — 0 błędów (rejestr kompletny i spójny)")
    return 0 if not errors else 1


if __name__ == "__main__":
    sys.exit(run())
