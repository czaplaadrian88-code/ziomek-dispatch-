#!/usr/bin/env python3
"""flag_lifecycle_seed — generator maszynowego rejestru CYKLU ŻYCIA flag (Z-P1-07 Faza A).

Rejestr lifecycle = warstwa METADANYCH ponad istniejącym `flag_registry` (silnik:
common.py 3-źródła + klasyfikacja intentional/service-scoped/known) ROZSZERZONA na
świat PANELU (`app/core/flags.py` DEFAULT_FLAGS + `flags.systemd.env` + drop-iny
nadajesz-panel) i APKI (`courier_api`/`courier_api_panelsync` + drop-iny courier-api),
plus ŚWIAT 1b silnika ZAMROŻONY w systemd (WSZYSTKIE `dispatch-*.service(.d)`, nie
tylko rdzeń-5) i LINKI BLIŹNIAKÓW cross-world.

Ten skaner NIE dubluje `flag_registry` — importuje jego skanery (`scan_common`,
`scan_decision_lists`, `load_flags_json`, `scan_code_tokens`, `_extract_paren_body`)
i klasyfikacje (`INTENTIONAL_PER_PROCESS`, `SERVICE_SCOPED`, `KNOWN_DIVERGENCES`).
Dokłada TYLKO to, czego `flag_registry` nie robi:
  • własny `_parse_systemd_env` (multi-para `Environment=A=1 B=1`; `flag_registry`
    gubi 2..n parę przez split("=",1) — patrz docs/flags/README „known-limitation"),
  • AST `DEFAULT_FLAGS` panelu (inny repo) + rozdzielenie tupli FP_EXTRA/TEST_ISOLATED,
  • skan APKI po nazwie ENV (kanoniczna tożsamość apki = nazwa env, nie stała modułu),
  • metadane lifecycle (owner/review/removal/rollback) + twins.

READ-ONLY wobec wszystkich źródeł (kanon repo, /etc/systemd, panel, courier_api,
żywy flags.json). NIE zmienia ŻADNEJ wartości flagi. Uruchamiany NA HOŚCIE; wynik
(`flag_lifecycle_registry.json`) commitowany. Deterministyczny (sort, stały indent).

Filtr sekretów: każda linia env odrzucana gdy nazwa zawiera TOKEN/SECRET/PASS/KEY/
DSN/CRED/COOKIE/AUTH lub wartość wygląda na URL/ścieżkę; logujemy TYLKO liczbę
odrzuconych (nigdy nazwy/wartości sekretów). W rejestrze wyłącznie flagi.

Użycie:
  python3 tools/flag_lifecycle_seed.py                    # seed → domyślna ścieżka
  python3 tools/flag_lifecycle_seed.py --out PLIK         # inna ścieżka wyjścia
  python3 tools/flag_lifecycle_seed.py --merge            # zachowaj ręczne `notes`
  python3 tools/flag_lifecycle_seed.py --flags-json PATH  # inny flags.json (test/host)
  python3 tools/flag_lifecycle_seed.py --systemd-dir D --panel-dir D --courier-dir D
"""
from __future__ import annotations

import argparse
import ast
import glob
import importlib.util
import json
import os
import re
import shlex
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
DISPATCH_V2 = os.path.dirname(_HERE)           # kod silnika = TEN worktree/kanon
SCRIPTS_ROOT = os.path.dirname(DISPATCH_V2)

# Domyślne ścieżki HOSTA (cross-repo/systemd są POZA worktree → absolutne, nadpisywalne).
DEF_FLAGS_JSON = "/root/.openclaw/workspace/scripts/flags.json"
DEF_SYSTEMD_DIR = "/etc/systemd/system"
DEF_PANEL_DIR = "/root/.openclaw/workspace/nadajesz_clone/panel/backend"
DEF_COURIER_DIR = "/root/.openclaw/workspace/scripts/courier_api"
DEF_PANELSYNC_DIR = "/root/.openclaw/workspace/scripts/courier_api_panelsync"
DEF_OUT = os.path.join(_HERE, "flag_lifecycle_registry.json")

SEED_DATE = "2026-07-10"
REVIEW_DATE = "2026-08-10"

# Filtr sekretów (nazwa env) + wzorce infra (nie-flagi) do świata 1b/panel/apka env.
_SECRET_RE = re.compile(r"TOKEN|SECRET|PASS|KEY|DSN|CRED|COOKIE|AUTH", re.I)
_INFRA_NAMES = {
    "PYTHONPATH", "PATH", "LD_LIBRARY_PATH", "HOME", "USER", "LANG", "LC_ALL",
    "TZ", "PYTHONUNBUFFERED", "PYTHONDONTWRITEBYTECODE", "VIRTUAL_ENV",
}
_FLAG_NAME_RE = re.compile(r"^[A-Z][A-Z0-9_]*$")

# ── BLIŹNIAKI cross-world (koncept → nazwy w rejestrze; nazwy RÓŻNE bo panel gubi
#    prefiks ENABLE_, a TRUST_CANON_ORDER↔BUILD_VIEW to głęboki rename). Seeder
#    linkuje twin_of DWUSTRONNIE i waliduje symetrię. ──────────────────────────
TWIN_CONCEPTS = {
    "delivery_dash_when_no_plan": ["DELIVERY_DASH_WHEN_NO_PLAN",
                                   "ENABLE_DELIVERY_DASH_WHEN_NO_PLAN"],
    "live_eta_courier_guard": ["LIVE_ETA_COURIER_GUARD",
                               "ENABLE_LIVE_ETA_COURIER_GUARD"],
    "plan_aware_podjazdy": ["PLAN_AWARE_PODJAZDY", "ENABLE_PLAN_AWARE_PODJAZDY"],
    "trust_canon_order": ["TRUST_CANON_ORDER", "ENABLE_BUILD_VIEW_TRUST_CANON_ORDER"],
    "live_eta_fresh_override_only": ["LIVE_ETA_FRESH_OVERRIDE_ONLY",
                                     "ENABLE_LIVE_ETA_FRESH_OVERRIDE_ONLY"],
}

# ── NOTATKI z weryfikacji dual-carrier 3× geocode (Z-P1-07 karta pkt 5). Consumer
#    czyta flags.json (C.flag(...)) z zamrożoną stałą modułu TYLKO jako default →
#    hot-reload POPRAWNY, NIE antywzorzec #9. Wynik grepa 2026-07-10, nie naprawiam.
GEOCODE_NOTES = {
    "ENABLE_GEOCODE_NOMINATIM_FALLBACK":
        "dual-carrier OK: geocoding.py czyta C.flag('ENABLE_GEOCODE_NOMINATIM_FALLBACK', "
        "C.ENABLE_GEOCODE_NOMINATIM_FALLBACK) — flags.json hot-reload wygrywa, stała "
        "modułu (env-frozen) = TYLKO default. NIE antywzorzec #9.",
    "ENABLE_GEOCODE_PIN_MEMORY_FALLBACK":
        "dual-carrier OK: geocoding.py czyta C.flag('ENABLE_GEOCODE_PIN_MEMORY_FALLBACK', "
        "C.ENABLE_GEOCODE_PIN_MEMORY_FALLBACK) — flags.json hot-reload wygrywa, stała "
        "modułu = default. NIE antywzorzec #9.",
    "ENABLE_GEOCODE_VERIFICATION_ENFORCE":
        "dual-carrier OK: geocoding.py czyta C.flag('ENABLE_GEOCODE_VERIFICATION_ENFORCE', "
        "C.ENABLE_GEOCODE_VERIFICATION_ENFORCE) — flags.json hot-reload wygrywa, stała "
        "modułu = default. NIE antywzorzec #9.",
}


# ── ładowanie flag_registry (jak flag_fingerprint_check: pakiet → fallback ścieżka)
def load_flag_registry():
    try:
        from dispatch_v2.tools import flag_registry as fr  # type: ignore
        return fr
    except Exception:
        p = os.path.join(_HERE, "flag_registry.py")
        spec = importlib.util.spec_from_file_location("_flag_registry_sib", p)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod


FR = load_flag_registry()


# ── własny parser systemd env (multi-para; secret-filter) ───────────────────────
def _parse_systemd_env(path: str):
    """Zwraca (pary, n_odrzuconych_sekretow). Pary = [(name, value)] z linii
    `Environment=…` — obsługuje `A=1 B=2` (shlex po whitespace) i cudzysłowy.
    Odsiewa sekrety i infra; do rejestru trafiają WYŁĄCZNIE flago-podobne."""
    pairs, secret_hits = [], 0
    try:
        lines = open(path, encoding="utf-8", errors="replace").read().splitlines()
    except OSError:
        return pairs, secret_hits
    for line in lines:
        line = line.strip()
        if not line.startswith("Environment="):
            continue
        body = line[len("Environment="):].strip()
        try:
            tokens = shlex.split(body)
        except ValueError:
            tokens = body.split()
        for tok in tokens:
            if "=" not in tok:
                continue
            k, v = tok.split("=", 1)
            k = k.strip()
            if _SECRET_RE.search(k) or "http" in v.lower():
                secret_hits += 1
                continue
            if not _is_flaglike_env(k, v):
                continue
            pairs.append((k, v))
    return pairs, secret_hits


def _is_flaglike_env(k: str, v: str) -> bool:
    if not _FLAG_NAME_RE.match(k) or k in _INFRA_NAMES:
        return False
    if v.startswith("/") or "http" in v.lower():
        return False
    # nazwy-nośniki plików/ścieżek/logów = infra, nie flaga
    if k.endswith(("_JSONL", "_OUT", "_DIR", "_PATH", "_FILE", "_LOG", "_URL")):
        return False
    return len(v) <= 40


def _norm_value(v: str):
    """'0'/'1'→bool, liczba→int/float, inaczej string (enum)."""
    if v in ("0", "1"):
        return v == "1"
    try:
        return int(v)
    except ValueError:
        pass
    try:
        return float(v)
    except ValueError:
        return v


# ── skan tupli common.py (rozdzielnie; reuse _extract_paren_body) ───────────────
_NAME_RE = re.compile(r'"([A-Z_][A-Z0-9_]*)"')


def _tuple_names(src: str, varname: str):
    body = FR._extract_paren_body(src, varname)
    body = re.sub(r"#[^\n]*", "", body)
    return tuple(_NAME_RE.findall(body))


# ── skan env-frozen modułowych (dowolny plik silnika/apki: NAME = ...environ.get)
_ENVFROZEN_RE = re.compile(
    r'^(?P<const>[A-Z][A-Z0-9_]*)\s*=\s*(?:float\(|int\()?(?:_os|os)\.environ\.get\(\s*'
    r'"(?P<env>[A-Z0-9_]+)"\s*,\s*"(?P<default>[^"]*)"\s*\)\s*\)?'
    r'(?P<cmp>\s*==\s*"1")?', re.M)


# Sufiksy nazw = nośnik infra (URL/ścieżka/host/user), NIE flaga-toggle.
_INFRA_SUFFIX = ("_URL", "_BASE", "_PATH", "_DIR", "_FILE", "_HOST", "_PORT", "_USER")


def _scan_envfrozen(py_files, key="env", bool_only=False):
    """{env|const: {'const','default','bool','file'}} z module-level
    `NAME = (int(|float()?(_os|os).environ.get("ENV","def")[== "1"])`.

    `bool_only=True` (SILNIK): flaga = boolowski TOGGLE (`== "1"`). Numeryczne/
    stringowe stałe env = KONFIG, nie flaga lifecycle — wchodzą do rejestru tylko
    przez flags.json/NUMERIC/TEST_ISO (świadome zawężenie, patrz raport §odstępstwa).
    `bool_only=False` (APKA): + numeryczne knoby zachowania (LIVE_ETA_MAX_AGE_MIN…).
    Zawsze odsiewa sekrety (TOKEN/SECRET/PASS/KEY/DSN/CRED…) + nośniki infra."""
    out = {}
    for f in sorted(py_files):
        try:
            src = open(f, encoding="utf-8", errors="replace").read()
        except OSError:
            continue
        rel = os.path.relpath(f, SCRIPTS_ROOT)
        for m in _ENVFROZEN_RE.finditer(src):
            const, env = m.group("const"), m.group("env")
            is_bool = bool(m.group("cmp"))
            raw = m.group("default")
            if bool_only and not is_bool:
                continue
            if _SECRET_RE.search(env) or _SECRET_RE.search(const):
                continue
            if env.endswith(_INFRA_SUFFIX) or const.endswith(_INFRA_SUFFIX):
                continue
            if raw.startswith(("http", "/")):
                continue
            name = env if key == "env" else const
            out[name] = {
                "const": const, "env": env,
                "default": (raw == "1") if is_bool else _norm_value(raw),
                "bool": is_bool, "file": rel,
            }
    return out


def _engine_py_files():
    files = glob.glob(os.path.join(DISPATCH_V2, "**", "*.py"), recursive=True)
    return [f for f in files
            if "eod_drafts" not in f and "__pycache__" not in f
            and ".bak" not in f and "/tests/" not in f and "/tools/" not in f]


def _build_consumer_index(py_files):
    """{token: set(relpath)} dla tokenów UPPER — file-level consumers (bez nr linii)."""
    idx = {}
    tok_re = re.compile(r"[A-Z][A-Z0-9_]{3,}")
    for f in py_files:
        try:
            src = open(f, encoding="utf-8", errors="replace").read()
        except OSError:
            continue
        rel = os.path.relpath(f, SCRIPTS_ROOT)
        for t in set(tok_re.findall(src)):
            idx.setdefault(t, set()).add(rel)
    return idx


# ── AST panel DEFAULT_FLAGS ─────────────────────────────────────────────────────
def _panel_default_flags(flags_py: str):
    """{name: default_bool} z `DEFAULT_FLAGS: dict = {...}` przez AST (inny repo)."""
    try:
        tree = ast.parse(open(flags_py, encoding="utf-8").read())
    except Exception:
        return {}
    out = {}
    for node in ast.walk(tree):
        targets = []
        if isinstance(node, ast.Assign):
            targets = node.targets
        elif isinstance(node, ast.AnnAssign):
            targets = [node.target]
        else:
            continue
        if not any(isinstance(t, ast.Name) and t.id == "DEFAULT_FLAGS" for t in targets):
            continue
        if not isinstance(node.value, ast.Dict):
            continue
        for k, v in zip(node.value.keys, node.value.values):
            if isinstance(k, ast.Constant) and isinstance(k.value, str):
                val = v.value if isinstance(v, ast.Constant) else None
                out[k.value] = val
    return out


def _panel_env_file(env_path: str):
    """{name: value} z flags.systemd.env (PANEL_FLAG_<name>=v) + n_sekretow."""
    out, secret_hits = {}, 0
    try:
        lines = open(env_path, encoding="utf-8", errors="replace").read().splitlines()
    except OSError:
        return out, secret_hits
    for line in lines:
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        k, v = k.strip(), v.strip().strip('"')
        if _SECRET_RE.search(k) or "http" in v.lower():
            secret_hits += 1
            continue
        if not k.startswith("PANEL_FLAG_"):
            continue
        out[k[len("PANEL_FLAG_"):]] = _norm_value(v)
    return out, secret_hits


# ── budowanie entry ─────────────────────────────────────────────────────────────
def _lifecycle(name, effective, in_fjson, code_tokens):
    if in_fjson and name not in code_tokens \
            and not any(p.match(name) for p in FR.DYNAMIC_KEY_FAMILIES):
        return "dead"
    if re.search(r"LEGACY|_OLD|DEPREC", name):
        return "deprecated"
    if "SHADOW" in name:
        return "shadow"
    if effective is True or (isinstance(effective, (int, float)) and effective) \
            or (isinstance(effective, str) and effective):
        return "live"
    return "planned"


def _removal(lifecycle):
    return {
        "live": "n/d-live",
        "shadow": "po walidacji shadow: flip lub retire (ACK Adrian)",
        "planned": "aktywacja lub retire wg roadmapy (ACK Adrian)",
        "dead": "kandydat retirementu — potwierdź brak dyn. czytelnika, usuń klucz",
        "deprecated": "retire zaplanowany (ACK Adrian)",
    }[lifecycle]


def _intentional(name):
    if name in FR.INTENTIONAL_PER_PROCESS:
        return {"value": True, "reason": "INTENTIONAL_PER_PROCESS (flag_registry): "
                "celowy split per-proces (własny cykl / telemetria shadow-only)."}
    if name in FR.SERVICE_SCOPED:
        owner, why = FR.SERVICE_SCOPED[name]
        return {"value": True,
                "reason": f"SERVICE_SCOPED owner={owner}: {why}"}
    return {"value": False, "reason": ""}


def build_registry(flags_json=DEF_FLAGS_JSON, systemd_dir=DEF_SYSTEMD_DIR,
                   panel_dir=DEF_PANEL_DIR, courier_dir=DEF_COURIER_DIR,
                   panelsync_dir=DEF_PANELSYNC_DIR):
    common_py = os.path.join(DISPATCH_V2, "common.py")
    src = open(common_py, encoding="utf-8").read()
    etap4 = _tuple_names(src, "ETAP4_DECISION_FLAGS")
    fp_extra = _tuple_names(src, "_FINGERPRINT_EXTRA_FLAGS")
    numeric = _tuple_names(src, "FLAGS_JSON_NUMERIC_OVERRIDES")
    test_iso = _tuple_names(src, "TEST_ISOLATED_INFRA_FLAGS")
    decision_all = set(etap4) | set(fp_extra)
    numeric_set = set(numeric)

    defs = FR.scan_common(common_py)
    lit = FR.scan_literal_defaults([n for n in decision_all | numeric_set | set(test_iso)
                                    if n not in defs], common_py)
    defs = {**lit, **defs}
    fjson = FR.load_flags_json(flags_json)

    engine_py = _engine_py_files()
    # SILNIK: tożsamość = nazwa ENV (=nazwa flagi w flags.json/systemd); tylko toggl.
    envfrozen_engine = _scan_envfrozen(engine_py, key="env", bool_only=True)
    code_tokens = FR.scan_code_tokens()
    consumer_idx = _build_consumer_index(engine_py)

    # ŚWIAT 1b: WSZYSTKIE dispatch-*.service(.d) — nie tylko rdzeń-5.
    engine_units_env = {}   # {unit: {name: value}}
    secret_total = 0
    unit_files = sorted(glob.glob(os.path.join(systemd_dir, "dispatch-*.service"))) \
        if os.path.isdir(systemd_dir) else []
    for main in unit_files:
        unit = os.path.basename(main)
        env = {}
        files = [main] + sorted(glob.glob(os.path.join(systemd_dir, unit + ".d", "*.conf")))
        for f in files:
            pairs, sh = _parse_systemd_env(f)
            secret_total += sh
            for k, v in pairs:
                env[k] = (_norm_value(v), os.path.basename(f))
        if env:
            engine_units_env[unit] = env

    entries = {}

    def _ensure(name):
        return entries.setdefault(name, {
            "name": name, "worlds": [], "carriers": [], "consumers": [],
            "twin_of": [], "notes": "",
        })

    # ── ENGINE: unia nazw z tupli ∪ flags.json ∪ env-frozen-module ∪ 1b-systemd ──
    engine_names = (decision_all | numeric_set | set(test_iso) | set(fjson)
                    | set(envfrozen_engine)
                    | {n for env in engine_units_env.values() for n in env})
    for name in sorted(engine_names):
        e = _ensure(name)
        e["worlds"].append("engine")
        is_decision = name in decision_all
        is_numeric = name in numeric_set
        d = envfrozen_engine.get(name) or defs.get(name)
        default = (d or {}).get("default")
        carriers, snap = [], {}
        # nośnik flags.json (kanon hot-reload dla decyzyjnych/numerycznych)
        if name in fjson:
            carriers.append("flags.json")
            snap["flags.json"] = fjson[name]
        # przynależność do tupli common.py
        if name in etap4:
            carriers.append("common.py:ETAP4_DECISION_FLAGS")
        if name in fp_extra:
            carriers.append("common.py:_FINGERPRINT_EXTRA_FLAGS")
        if name in numeric_set:
            carriers.append("common.py:FLAGS_JSON_NUMERIC_OVERRIDES")
        if name in test_iso:
            carriers.append("common.py:TEST_ISOLATED_INFRA_FLAGS")
        if name in envfrozen_engine:
            carriers.append(f"{envfrozen_engine[name]['file']}-const")
        elif name in defs and name not in fjson and not (is_decision or is_numeric):
            carriers.append("common.py-const")
        # świat 1b: pin w unitach (per-SERVICE — parity-guardy pinują env dla SWOICH proc.)
        pin_units = []
        for unit, env in sorted(engine_units_env.items()):
            if name in env:
                val, conf = env[name]
                snap[unit] = val
                carriers.append(f"drop-in:{unit}.d/{conf}" if conf != unit
                                else f"unit:{unit}")
                pin_units.append(unit)
        # source_of_truth
        if name in fjson and (is_decision or is_numeric):
            sot = "flags.json"
        elif name in fjson:
            sot = "flags.json"
        elif pin_units:
            sot = f"drop-in:{pin_units[0]}"
        elif name in envfrozen_engine:
            sot = "common.py-const" if envfrozen_engine[name]["file"].endswith("common.py") \
                else f"{envfrozen_engine[name]['file']}"
        else:
            sot = "common.py-const"
        # effective (kanon): flags.json dla decyzyjnych; inaczej default / per-service
        if "flags.json" in snap:
            effective = snap["flags.json"]
        elif pin_units:
            effective = snap[pin_units[0]]
        else:
            effective = default
        if not snap:
            snap["default"] = default
        # owner
        if name in FR.SERVICE_SCOPED:
            owner_svc = FR.SERVICE_SCOPED[name][0]
        elif len(pin_units) == 1:
            owner_svc = pin_units[0]
        else:
            owner_svc = "dispatch-shadow.service"
        lifecycle = _lifecycle(name, effective, name in fjson, code_tokens)
        # rollback
        if name in fjson and (is_decision or is_numeric):
            rollback = "flags.json OFF hot-reload (bez restartu)"
        elif pin_units:
            rollback = (f"rm drop-in {carriers[-1].split(':',1)[-1]} + restart "
                        f"{pin_units[0]} ZA ACK")
        else:
            rollback = f"env OFF w {sot} + restart {owner_svc} ZA ACK"
        cons = sorted(consumer_idx.get(name, set()))
        if name in envfrozen_engine:
            cons = sorted(set(cons) |
                          {f"{envfrozen_engine[name]['file']}:{envfrozen_engine[name]['const']}"})
        e.update({
            "source_of_truth": sot,
            "carriers": _dedup(e["carriers"] + carriers),
            "owner": {"service": owner_svc, "business": "Adrian"},
            "lifecycle": lifecycle, "lifecycle_seeded": True,
            "default": default,
            "current_snapshot": snap,
            "consumers": cons,
            "rollback": rollback,
            "review_date": REVIEW_DATE,
            "removal_condition": _removal(lifecycle),
            "intentional_per_process": _intentional(name),
            "known_drift": name in FR.KNOWN_DIVERGENCES,
            "known_drift_note": FR.KNOWN_DIVERGENCES.get(name, ""),
        })
        if name in GEOCODE_NOTES:
            e["notes"] = GEOCODE_NOTES[name]

    # ── PANEL (skip-if-absent) ──────────────────────────────────────────────────
    panel_secret = 0
    flags_py = os.path.join(panel_dir, "app", "core", "flags.py")
    env_file = os.path.join(panel_dir, "flags.systemd.env")
    if os.path.isfile(flags_py):
        default_flags = _panel_default_flags(flags_py)
        env_flags, panel_secret = _panel_env_file(env_file)
        # drop-iny panelu
        panel_dropin = {}
        for f in sorted(glob.glob(os.path.join(systemd_dir,
                                               "nadajesz-panel.service.d", "*.conf"))):
            pairs, sh = _parse_systemd_env(f)
            panel_secret += sh
            for k, v in pairs:
                if k.startswith("PANEL_FLAG_"):
                    panel_dropin[k[len("PANEL_FLAG_"):]] = (_norm_value(v),
                                                            os.path.basename(f))
        for name in sorted(set(default_flags) | set(env_flags) | set(panel_dropin)):
            e = _ensure(name)
            e["worlds"].append("panel")
            default = default_flags.get(name)
            carriers = ["DEFAULT_FLAGS"]
            snap, sot, dropin_conf = {}, "DEFAULT_FLAGS", None
            eff = default
            if name in env_flags:
                carriers.append("flags.systemd.env")
                eff = env_flags[name]
                sot = "flags.systemd.env"
            if name in panel_dropin:
                val, conf = panel_dropin[name]
                carriers.append(f"drop-in:nadajesz-panel.service.d/{conf}")
                eff = val
                sot = f"drop-in:nadajesz-panel.service.d/{conf}"
                dropin_conf = conf
            snap["nadajesz-panel.service"] = eff
            lifecycle = _lifecycle(name, eff, False, code_tokens)
            rollback = (f"rm drop-in {dropin_conf} + restart nadajesz-panel.service ZA ACK"
                        if dropin_conf else
                        (f"PANEL_FLAG_{name} w flags.systemd.env + restart nadajesz-panel.service"
                         if name in env_flags else
                         "DEFAULT_FLAGS w app/core/flags.py (deploy panelu)"))
            e.update({
                "source_of_truth": sot, "carriers": _dedup(e["carriers"] + carriers),
                "owner": {"service": "nadajesz-panel.service", "business": "Adrian"},
                "lifecycle": lifecycle, "lifecycle_seeded": True, "default": default,
                "current_snapshot": {**e.get("current_snapshot", {}), **snap},
                "consumers": _dedup(e["consumers"] + [f"panel:app/core/flags.py:{name}"]),
                "rollback": rollback, "review_date": REVIEW_DATE,
                "removal_condition": _removal(lifecycle),
                "intentional_per_process": e.get("intentional_per_process",
                                                 {"value": False, "reason": ""}),
                "known_drift": e.get("known_drift", False),
                "known_drift_note": e.get("known_drift_note", ""),
            })
    else:
        panel_secret = -1  # sygnał „skip — nieobecny"

    # ── APKA (skip-if-absent; tożsamość = nazwa ENV) ────────────────────────────
    apka_secret = 0
    courier_py = (glob.glob(os.path.join(courier_dir, "**", "*.py"), recursive=True)
                  if os.path.isdir(courier_dir) else [])
    courier_py += (glob.glob(os.path.join(panelsync_dir, "**", "*.py"), recursive=True)
                   if os.path.isdir(panelsync_dir) else [])
    courier_py = [f for f in courier_py if "__pycache__" not in f and ".bak" not in f]
    if courier_py:
        apka_env = _scan_envfrozen(courier_py, key="env", bool_only=False)
        apka_dropin = {}
        for f in sorted(glob.glob(os.path.join(systemd_dir,
                                               "courier-api.service.d", "*.conf"))):
            pairs, sh = _parse_systemd_env(f)
            apka_secret += sh
            for k, v in pairs:
                apka_dropin[k] = (_norm_value(v), os.path.basename(f))
        for name in sorted(set(apka_env) | set(apka_dropin)):
            e = _ensure(name)
            e["worlds"].append("apka")
            info = apka_env.get(name)
            default = (info or {}).get("default")
            carriers, snap, sot, dropin_conf = [], {}, "courier_api/config.py", None
            eff = default
            if info:
                carriers.append("courier_api/config.py")
            if name in apka_dropin:
                val, conf = apka_dropin[name]
                carriers.append(f"drop-in:courier-api.service.d/{conf}")
                eff = val
                sot = f"drop-in:courier-api.service.d/{conf}"
                dropin_conf = conf
            snap["courier-api.service"] = eff
            lifecycle = _lifecycle(name, eff, False, code_tokens)
            cons = [f"{info['file']}:{info['const']}"] if info else []
            rollback = (f"rm drop-in {dropin_conf} + restart courier-api.service ZA ACK"
                        if dropin_conf else
                        f"env {name} OFF + restart courier-api.service ZA ACK")
            e.update({
                "source_of_truth": sot, "carriers": _dedup(e["carriers"] + carriers),
                "owner": {"service": "courier-api.service", "business": "Adrian"},
                "lifecycle": lifecycle, "lifecycle_seeded": True, "default": default,
                "current_snapshot": {**e.get("current_snapshot", {}), **snap},
                "consumers": _dedup(e["consumers"] + cons),
                "rollback": rollback, "review_date": REVIEW_DATE,
                "removal_condition": _removal(lifecycle),
                "intentional_per_process": e.get("intentional_per_process",
                                                 {"value": False, "reason": ""}),
                "known_drift": e.get("known_drift", False),
                "known_drift_note": e.get("known_drift_note", ""),
            })
    else:
        apka_secret = -1

    # ── TWINS (dwustronnie) ─────────────────────────────────────────────────────
    for _concept, members in TWIN_CONCEPTS.items():
        present = [m for m in members if m in entries]
        for m in present:
            entries[m]["twin_of"] = sorted(set(entries[m]["twin_of"])
                                           | (set(present) - {m}))

    # dedup worlds/carriers/consumers + sort deterministyczny
    for e in entries.values():
        e["worlds"] = sorted(set(e["worlds"]))
        e["carriers"] = _dedup(e["carriers"])
        e["consumers"] = sorted(set(e["consumers"]))
        e["twin_of"] = sorted(set(e["twin_of"]))
        e.setdefault("notes", "")

    meta = {
        "generator": "tools/flag_lifecycle_seed.py",
        "seed_date": SEED_DATE, "review_date": REVIEW_DATE,
        "counts": {
            "total": len(entries),
            "engine": sum(1 for e in entries.values() if "engine" in e["worlds"]),
            "panel": sum(1 for e in entries.values() if "panel" in e["worlds"]),
            "apka": sum(1 for e in entries.values() if "apka" in e["worlds"]),
            "flags_json_keys": len(fjson),
            "etap4": len(etap4), "fp_extra": len(fp_extra),
            "numeric": len(numeric), "test_isolated": len(test_iso),
            "engine_1b_units": len(engine_units_env),
            "twins_concepts": len(TWIN_CONCEPTS),
            "known_drift": sum(1 for e in entries.values() if e.get("known_drift")),
        },
        "secret_lines_rejected": {
            "engine_1b": secret_total,
            "panel": panel_secret, "apka": apka_secret,
        },
        "note": ("stan flag = 3 światy (ADR-004); wartości current_snapshot z żywych "
                 "źródeł dnia " + SEED_DATE + "; lifecycle heurystyczny (lifecycle_seeded)."),
    }
    return {"_meta": meta, "flags": entries}


def _dedup(seq):
    seen, out = set(), []
    for x in seq:
        if x not in seen:
            seen.add(x)
            out.append(x)
    return out


def dumps(reg) -> str:
    return json.dumps(reg, indent=2, ensure_ascii=False, sort_keys=True) + "\n"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=DEF_OUT)
    ap.add_argument("--flags-json", default=DEF_FLAGS_JSON)
    ap.add_argument("--systemd-dir", default=DEF_SYSTEMD_DIR)
    ap.add_argument("--panel-dir", default=DEF_PANEL_DIR)
    ap.add_argument("--courier-dir", default=DEF_COURIER_DIR)
    ap.add_argument("--panelsync-dir", default=DEF_PANELSYNC_DIR)
    ap.add_argument("--merge", action="store_true",
                    help="zachowaj ręczne `notes` z istniejącego rejestru")
    args = ap.parse_args()
    reg = build_registry(args.flags_json, args.systemd_dir, args.panel_dir,
                         args.courier_dir, args.panelsync_dir)
    if args.merge and os.path.isfile(args.out):
        try:
            old = json.load(open(args.out, encoding="utf-8")).get("flags", {})
            for name, e in reg["flags"].items():
                on = old.get(name, {})
                if on.get("notes") and not e.get("notes"):
                    e["notes"] = on["notes"]
        except Exception:
            pass
    with open(args.out, "w", encoding="utf-8") as f:
        f.write(dumps(reg))
    m = reg["_meta"]["counts"]
    print(f"SEED → {args.out}")
    print(f"  flag: {m['total']} (engine={m['engine']}, panel={m['panel']}, "
          f"apka={m['apka']})")
    print(f"  engine: flags.json={m['flags_json_keys']} ETAP4={m['etap4']} "
          f"FP_EXTRA={m['fp_extra']} NUMERIC={m['numeric']} "
          f"TEST_ISO={m['test_isolated']} 1b-units={m['engine_1b_units']}")
    print(f"  twins-konceptów={m['twins_concepts']} known_drift={m['known_drift']}")
    print(f"  sekrety odrzucone: {reg['_meta']['secret_lines_rejected']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
