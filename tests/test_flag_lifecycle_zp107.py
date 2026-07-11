"""Z-P1-07 Faza A — test CI rejestru cyklu życia flag (HERMETYCZNY).

ZERO odczytu hosta: /etc, dispatch_state, journalctl, żywy flags.json. Czyta
WYŁĄCZNIE: commitowany `tools/flag_lifecycle_registry.json`, worktree `common.py`
(source-parse), fixtury tmp. Cross-repo (panel/apka/systemd) wymuszony na SKIP
przez nieistniejące ścieżki. Dowodzi: (1) struktura pełna, (2) twins dwustronne,
(3) coverage silnika NIEZALEŻNIE od rejestru (anty-tautologia), (4) checker
zielony na spójnych danych, (5) checker ŁAPIE regresje (flaga-ON≠OFF), (6)
cross-repo skip-safe.
"""
from __future__ import annotations

import importlib.util
import json
import os

import pytest

_HERE = os.path.dirname(os.path.abspath(__file__))
DV2 = os.path.dirname(_HERE)
TOOLS = os.path.join(DV2, "tools")
REGISTRY = os.path.join(TOOLS, "flag_lifecycle_registry.json")
COMMON_PY = os.path.join(DV2, "common.py")
NONEXIST = "/nonexistent_flag_lifecycle_ci_xyz"


def _load_by_path(name, filename):
    p = os.path.join(TOOLS, filename)
    spec = importlib.util.spec_from_file_location(name, p)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


CHK = _load_by_path("_flc_check_test", "flag_lifecycle_check.py")
SD = _load_by_path("_flc_seed_test", "flag_lifecycle_seed.py")


def _registry():
    with open(REGISTRY, encoding="utf-8") as f:
        return json.load(f)


def _flags_json_from_registry(tmp_path, reg, extra=None, flip=None):
    """flags.json spójny z rejestrem (wartości z current_snapshot['flags.json']).
    extra: dodatkowy klucz (test sieroty). flip: {name: nowa_wartość} (test dryfu)."""
    d = {}
    for name, e in reg["flags"].items():
        snap = e.get("current_snapshot", {})
        if "flags.json" in snap:
            d[name] = snap["flags.json"]
    if extra:
        d.update(extra)
    if flip:
        d.update(flip)
    p = tmp_path / "flags.json"
    p.write_text(json.dumps(d, ensure_ascii=False, indent=2), encoding="utf-8")
    return str(p)


def _run(argv):
    """run() checkera z wymuszonym SKIP cross-repo (nieistniejące ścieżki)."""
    base = ["--registry", REGISTRY, "--common-py", COMMON_PY,
            "--panel-dir", NONEXIST, "--courier-dir", NONEXIST,
            "--panelsync-dir", NONEXIST, "--systemd-dir", NONEXIST]
    return CHK.run(base + argv)


# ── 1) STRUKTURA ────────────────────────────────────────────────────────────────
def test_structure_complete():
    reg = _registry()
    assert reg["flags"], "rejestr pusty"
    errs = CHK.check_structure(reg)
    assert errs == [], f"błędy strukturalne: {errs[:10]}"
    # każdy wpis ma komplet pól i legalny lifecycle
    for name, e in reg["flags"].items():
        assert CHK.REQUIRED_FIELDS <= set(e), f"{name}: brak pól"
        assert e["lifecycle"] in CHK.ALLOWED_LIFECYCLE
        assert e["name"] == name
        # pre-kuracja: seeded=True; po kuracji (2026-07-10): curated_at ⇔ seeded=False
        assert isinstance(e["lifecycle_seeded"], bool)
        if e.get("curated_at"):
            assert e["lifecycle_seeded"] is False, f"{name}: curated_at + seeded=True"
        else:
            assert e["lifecycle_seeded"] is True, f"{name}: bez kuracji a seeded=False"


# ── 2) TWINS dwustronne (w tym para RÓŻNO-NAZWA) ────────────────────────────────
def test_twins_bidirectional():
    reg = _registry()
    flags = reg["flags"]
    for name, e in flags.items():
        for t in e["twin_of"]:
            assert t in flags, f"{name}: twin {t} nie istnieje"
            assert name in flags[t]["twin_of"], f"{name}↔{t} nie dwustronny"
    # para o RÓŻNEJ nazwie MUSI być zlinkowana
    a, b = "TRUST_CANON_ORDER", "ENABLE_BUILD_VIEW_TRUST_CANON_ORDER"
    assert b in flags[a]["twin_of"] and a in flags[b]["twin_of"]
    assert flags[a]["worlds"] == ["panel"] and flags[b]["worlds"] == ["apka"]


# ── 3) COVERAGE SILNIKA niezależny od rejestru (anty-tautologia) ────────────────
def test_engine_coverage_independent():
    reg = _registry()
    flags = reg["flags"]
    src = open(COMMON_PY, encoding="utf-8").read()
    universe = set()
    for tup in ("ETAP4_DECISION_FLAGS", "_FINGERPRINT_EXTRA_FLAGS",
                "FLAGS_JSON_NUMERIC_OVERRIDES", "TEST_ISOLATED_INFRA_FLAGS"):
        universe |= set(SD._tuple_names(src, tup))
    assert len(universe) > 150, "source-parse tupli podejrzanie mały"
    missing = [n for n in universe
               if n not in flags or "engine" not in flags[n]["worlds"]]
    assert missing == [], f"flagi silnika bez wpisu engine: {missing[:15]}"


# ── 4) CHECKER ZIELONY na spójnych danych ───────────────────────────────────────
def test_checker_green_on_consistent(tmp_path):
    reg = _registry()
    fj = _flags_json_from_registry(tmp_path, reg)
    rc = _run(["--flags-json", fj])
    assert rc == 0


def test_checker_green_skip_external():
    assert _run(["--skip-external"]) == 0


# ── 5) CHECKER ŁAPIE REGRESJE (flaga-ON≠OFF) ────────────────────────────────────
def _corrupt_registry(tmp_path, mutate):
    reg = _registry()
    mutate(reg["flags"])
    p = tmp_path / "reg.json"
    p.write_text(json.dumps(reg, ensure_ascii=False, indent=2), encoding="utf-8")
    return str(p)


def test_catches_missing_etap4_entry(tmp_path):
    def m(f):
        del f["ENABLE_OBJ_SPAN_COST"]  # flaga ETAP4 znika z rejestru
    p = _corrupt_registry(tmp_path, m)
    errs = CHK.check_engine_coverage(_load_json(p), COMMON_PY)
    assert any("ENABLE_OBJ_SPAN_COST" in e and "COVERAGE" in e for e in errs)


def test_catches_broken_twin(tmp_path):
    def m(f):
        f["ENABLE_BUILD_VIEW_TRUST_CANON_ORDER"]["twin_of"] = []
    p = _corrupt_registry(tmp_path, m)
    errs = CHK.check_structure(_load_json(p))
    assert any("TWIN" in e for e in errs)


def test_catches_missing_field(tmp_path):
    def m(f):
        f["ENABLE_FROZEN_PICKUP_ETA"].pop("rollback", None)
    p = _corrupt_registry(tmp_path, m)
    errs = CHK.check_structure(_load_json(p))
    assert any("POLA" in e and "ENABLE_FROZEN_PICKUP_ETA" in e for e in errs)


def test_catches_flags_json_orphan(tmp_path):
    reg = _registry()
    fj = _flags_json_from_registry(tmp_path, reg,
                                   extra={"ZZZ_FLAG_NOT_IN_REGISTRY": True})
    errs = CHK.check_flags_json(reg, fj)
    assert any("SIEROTA" in e and "ZZZ_FLAG_NOT_IN_REGISTRY" in e for e in errs)


def test_catches_flags_json_drift(tmp_path):
    reg = _registry()
    # znajdź flagę z boolowskim flags.json i odwróć wartość w źródle
    target = next(n for n, e in reg["flags"].items()
                  if isinstance(e["current_snapshot"].get("flags.json"), bool))
    cur = reg["flags"][target]["current_snapshot"]["flags.json"]
    fj = _flags_json_from_registry(tmp_path, reg, flip={target: (not cur)})
    errs = CHK.check_flags_json(reg, fj)
    assert any("DRYF" in e and target in e for e in errs)


def test_corrupt_registry_nonzero_exit(tmp_path):
    p = _corrupt_registry(tmp_path, lambda f: f.pop("ENABLE_OBJ_SPAN_COST"))
    rc = CHK.run(["--registry", p, "--common-py", COMMON_PY, "--skip-external"])
    assert rc == 1


# ── 6) CROSS-REPO SKIP-SAFE (CI bez panel/apka/systemd) ─────────────────────────
def test_cross_repo_skip_safe():
    reg = _registry()
    errs, skips = CHK.check_cross_repo(reg, NONEXIST, NONEXIST, NONEXIST, NONEXIST)
    assert errs == [], "nieobecny cross-repo NIE może dawać błędów"
    assert len(skips) >= 3, "powinny być skipy panel/apka/systemd"


def test_use_v2_parser_historical_drift_is_closed(tmp_path):
    """Po flipie 10.07 USE_V2_PARSER nie może pozostać wyjątkiem known_drift."""
    reg = _registry()
    entry = reg["flags"]["USE_V2_PARSER"]
    assert entry["known_drift"] is False
    assert "DOMKNIĘTY" in entry["known_drift_note"]
    assert _run(["--flags-json", _flags_json_from_registry(tmp_path, reg)]) == 0


def test_generic_known_drift_is_reported_not_error(tmp_path):
    """Sam kontrakt known_drift zostaje przetestowany na syntetycznym dryfcie."""
    reg = _registry()
    target = "USE_V2_PARSER"
    reg["flags"][target]["known_drift"] = True
    current = reg["flags"][target]["current_snapshot"]["flags.json"]
    flags_json = _flags_json_from_registry(tmp_path, reg, flip={target: not current})
    systemd_dir = tmp_path / "systemd"
    systemd_dir.mkdir()
    real, known, info = CHK.check_live(reg, flags_json, str(systemd_dir), False)
    assert real == [] and info == []
    assert len(known) == 1 and target in known[0]


def _load_json(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


# ── 8) KURACJA (ACK Adrian 2026-07-10) ──────────────────────────────────────────
def test_curation_complete_on_committed_registry():
    """Rejestr po kuracji: 100% wpisów curated_at + lifecycle_seeded=False +
    owner (service+business=Adrian) + review_date + removal_condition."""
    reg = _registry()
    fl = reg["flags"]
    assert fl, "pusty rejestr"
    missing = [n for n, e in fl.items()
               if not e.get("curated_at")
               or e.get("lifecycle_seeded") is not False
               or not (e.get("owner") or {}).get("service")
               or (e.get("owner") or {}).get("business") != "Adrian"
               or not e.get("review_date")
               or not e.get("removal_condition")]
    assert not missing, f"wpisy bez pełnej kuracji: {missing[:10]} (+{max(0, len(missing)-10)})"


def test_reseed_merge_preserves_curation_pure():
    """RE-SEED --merge nie może zabić kuracji: pola kuracji ze STAREGO wpisu
    (curated_at) wygrywają, pola DERYWOWANE (snapshot/default) idą ze świeżego
    skanu; wpis bez curated_at w starym = nietknięty przez merge."""
    fresh = {"flags": {
        "FLAG_A": {"name": "FLAG_A", "lifecycle": "planned", "lifecycle_seeded": True,
                   "owner": {}, "review_date": "2026-08-10", "removal_condition": "seed",
                   "notes": "", "default": False,
                   "current_snapshot": {"flags.json": True}},
        "FLAG_B": {"name": "FLAG_B", "lifecycle": "planned", "lifecycle_seeded": True,
                   "owner": {}, "review_date": "2026-08-10", "removal_condition": "seed",
                   "notes": "", "default": False,
                   "current_snapshot": {"flags.json": False}},
    }}
    old = {
        "FLAG_A": {"name": "FLAG_A", "curated_at": "2026-07-10",
                   "lifecycle": "live", "lifecycle_seeded": False,
                   "owner": {"service": "dispatch-shadow.service", "business": "Adrian"},
                   "review_date": "2026-10-10", "removal_condition": "n/d dopóki live",
                   "notes": "kuracja-test", "default": "STARY-DERYWAT",
                   "current_snapshot": {"flags.json": "STARY-DERYWAT"}},
        # FLAG_B w starym BEZ kuracji → merge nie dotyka
        "FLAG_B": {"name": "FLAG_B", "lifecycle": "planned", "lifecycle_seeded": True,
                   "owner": {}, "notes": ""},
    }
    preserved = SD.merge_curation(fresh, old)
    assert preserved == 1
    a = fresh["flags"]["FLAG_A"]
    # pola KURACJI ze starego:
    assert a["curated_at"] == "2026-07-10"
    assert a["lifecycle"] == "live" and a["lifecycle_seeded"] is False
    assert a["owner"]["business"] == "Adrian" and a["review_date"] == "2026-10-10"
    assert a["removal_condition"] == "n/d dopóki live" and a["notes"] == "kuracja-test"
    # pola DERYWOWANE ze świeżego skanu (NIE ze starego):
    assert a["default"] is False and a["current_snapshot"] == {"flags.json": True}
    b = fresh["flags"]["FLAG_B"]
    assert b["lifecycle_seeded"] is True and not b.get("curated_at")
