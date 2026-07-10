"""Z-P1-05 Faza A — registry + normalize strategies (hermetic; fixtures only).

Zero reads of the live dispatch_state; every source is an anonymized fixture in
tests/fixtures/identity/. Fixtures reproduce the real case classes with invented
surnames (bare-key poison, Kuba/Jakub double alias, diacritic Ś vs ascii abbrev,
missing name/tier, duplicate/orphan PIN, coordinator).
"""
import json
import logging
from pathlib import Path

import pytest

from dispatch_v2.identity import normalize, registry, schema, sources

FIX = Path(__file__).parent / "fixtures" / "identity"


def _paths():
    return {
        "state_root": str(FIX),
        "repo_root": str(FIX),
        "kurier_ids": str(FIX / "kurier_ids.json"),
        "kurier_piny": str(FIX / "kurier_piny.json"),
        "courier_names": str(FIX / "courier_names.json"),
        "courier_tiers": str(FIX / "courier_tiers.json"),
        "grafik_full_names": str(FIX / "grafik_full_names.json"),
        "shift_ignored": str(FIX / "shift_ignored_names.json"),
        "whitelist": str(FIX / "courier_whitelist_v1.json"),
        "courier_api_db": str(FIX / "does_not_exist.db"),
        "daily_full_names": str(FIX / "daily_kurier_full_names_live.json"),
    }


def _excluded():
    return json.loads((FIX / "excluded_cids.json").read_text())


def _bundle():
    return sources.load_all(_paths(), excluded_source=_excluded(), with_sqlite=False)


def _n(x):
    return None if x is None else schema.canon_cid(x)


# --------------------------------------------------------------------------- #
# norm contract
# --------------------------------------------------------------------------- #


def test_norm_strips_punct_ws_lower():
    assert normalize.norm("  Ch. ") == "ch"
    assert normalize.norm("Adam No,") == "adam no"
    assert normalize.norm(None) == ""


def test_norm_does_not_fold_diacritics():
    # cid 376 landmine: an ascii abbreviation must NOT match a diacritic surname.
    assert normalize.norm("Ściepko") == "ściepko"
    assert normalize.norm("Ściepko") != normalize.norm("Sciepko")
    assert normalize.norm("Paweł") == "paweł"


# --------------------------------------------------------------------------- #
# worker strategy (×10 / ×5)
# --------------------------------------------------------------------------- #


def test_worker_exact_and_case_insensitive():
    ids = {"Adrian Cit": 457, "Adrian": 21}
    assert normalize.resolve_worker("Adrian Cit", ids) == 457       # exact
    assert normalize.resolve_worker("adrian cit", ids) == 457       # case-insensitive


def test_worker_score_and_bare_key():
    ids = {"Adrian Cit": 457, "Adrian": 21}
    assert normalize.resolve_worker("Adrian Citko", ids) == 457     # surname prefix
    assert normalize.resolve_worker("Adrian Xyz", ids) == 21        # falls to bare key (score 1)


def test_worker_tie_is_ambiguous_none():
    ids = {"Anna Kowalska": 1, "Anna Kowalczyk": 2}
    assert normalize.resolve_worker("Anna Kow", ids) is None        # tie -> ambiguous


def test_worker_bare_key_strict_filters_single_word():
    ids = {"Adam": 100, "Adam Nowak": 100, "Adam No": 100}
    # non-strict: a stranger "Adam Zzz" is swallowed by the bare key
    assert normalize.resolve_worker("Adam Zzz", ids) == 100
    # strict: single-word keys filtered -> no surname match -> None
    assert normalize.resolve_worker("Adam Zzz", ids, bare_key_strict=True) is None
    # strict still resolves a real surname match
    assert normalize.resolve_worker("Adam Nowak", ids, bare_key_strict=True) == 100


# --------------------------------------------------------------------------- #
# panel_roster strategy (×10 / ×10) + documented divergence from worker
# --------------------------------------------------------------------------- #


def test_panel_roster_match_none_ambiguous():
    roster = {300: "Anna Kowalska", 301: "Anna Kotecka"}
    assert normalize.resolve_panel_roster("Anna Kowalska", roster).cid == 300
    assert normalize.resolve_panel_roster("Zenon Nieznany", roster).status == "none"
    amb = normalize.resolve_panel_roster("Anna Ko", {1: "Anna Kowalska", 2: "Anna Kotecka"})
    assert amb.status == "ambiguous"


def test_worker_and_panel_roster_diverge_by_design():
    # Reverse-prefix direction: worker ×5, panel ×10. Constructed so a forward
    # candidate (×10 both) TIES the reverse candidate under worker but LOSES
    # under panel — proving both semantics are preserved 1:1 (NOT unified).
    ids = {"Ola K": 700, "Ola Kowalska": 701}
    roster = {700: "Ola K", 701: "Ola Kowalska"}
    assert normalize.score_worker_alias("Ola Ko", "Ola K") == 10        # forward len('k')*10
    assert normalize.score_worker_alias("Ola Ko", "Ola Kowalska") == 10  # reverse len('ko')*5
    assert normalize.resolve_worker("Ola Ko", ids) is None              # tie -> ambiguous
    assert normalize.score_panel_roster("Ola Ko", "Ola Kowalska") == 20  # reverse len('ko')*10
    assert normalize.resolve_panel_roster("Ola Ko", roster).cid == 701  # panel resolves


# --------------------------------------------------------------------------- #
# registry build
# --------------------------------------------------------------------------- #


def test_build_registry_records_and_provenance():
    reg = registry.build_registry(_bundle())
    # universe: 12 distinct CIDs across the fixtures
    assert len(reg.records) == 12
    assert all(isinstance(c, str) for c in reg.records)  # cid canonical str

    r = reg.by_cid("100")
    assert r is not None
    assert set(r.aliases["ids"]) == {"Adam", "Adam Nowak", "Adam No"}
    assert r.aliases["grafik"] == ["Adam Nowak"]
    assert r.aliases["panel"] == ["Adam Nowak"]
    assert r.full_name["grafik"] == "Adam Nowak"
    assert r.full_name["accounting"] == "Adam Nowakowski"
    assert r.best_full_name() == "Adam Nowak"
    assert r.tier == "gold"
    assert r.pin_present is True
    assert r.pin_last2 in {"00", "01"}
    assert r.active is True
    assert r.excluded is False
    assert r.is_coordinator is False


def test_registry_coordinator_excluded_inactive_flags():
    reg = registry.build_registry(_bundle())
    assert reg.by_cid("26").is_coordinator is True     # via COORDINATOR_CIDS
    assert reg.by_cid("999").is_coordinator is True     # via tier coordinator flag
    assert reg.by_cid("26").excluded is True            # excluded_cids
    assert reg.by_cid("110").excluded is True
    assert reg.by_cid("240").active is False            # shift_ignored name


def test_registry_resolve_profiles():
    reg = registry.build_registry(_bundle())
    assert reg.resolve("Adam Nowak", "worker") == "100"           # exact via kurier_ids
    assert reg.resolve("Anna Kowalska", "panel_roster") == "300"  # roster match
    with pytest.raises(ValueError):
        reg.resolve("x", "bogus_profile")


def test_pin_never_plaintext_in_records():
    reg = registry.build_registry(_bundle())
    for r in reg.all_records():
        if r.pin_last2 is not None:
            assert len(r.pin_last2) <= 2
        assert "pin" not in r.to_public_dict()  # only pin_present / pin_last2 surface


def test_schema_validation_clean_on_built_records():
    reg = registry.build_registry(_bundle())
    for r in reg.all_records():
        assert schema.validate_record(r) == []


# --------------------------------------------------------------------------- #
# fail-open + optional sqlite
# --------------------------------------------------------------------------- #


def test_fail_open_on_all_missing_sources():
    paths = {k: str(FIX / "nope" / f"{k}.json") for k in _paths()}
    paths["state_root"] = str(FIX / "nope")
    paths["repo_root"] = str(FIX / "nope")
    paths["courier_api_db"] = str(FIX / "nope.db")
    b = sources.load_all(paths, excluded_source=[], with_sqlite=False)
    reg = registry.build_registry(b)   # must not raise
    assert reg.records == {}
    assert b.notes  # missing-source notes recorded


def test_sqlite_source_optional():
    assert sources.load_courier_api_names("/nonexistent-path.db") is None


# --------------------------------------------------------------------------- #
# optional legacy parity on fixtures (guarded — skips if legacy import fails)
# --------------------------------------------------------------------------- #


def test_legacy_parity_on_fixtures(monkeypatch):
    try:
        import dispatch_v2.common as common
        monkeypatch.setattr(
            common, "setup_logger",
            lambda *a, **k: logging.getLogger("identity.parity.noop"), raising=False,
        )
    except Exception:  # pragma: no cover
        pytest.skip("dispatch_v2.common unavailable")
    try:
        import dispatch_v2.shift_notifications.state as st
        monkeypatch.setattr(st, "append_match_debug_log", lambda *a, **k: None, raising=False)
    except Exception:
        pass
    try:
        from dispatch_v2.shift_notifications.worker import resolve_cid as legacy_worker
    except Exception as e:  # pragma: no cover
        pytest.skip(f"legacy worker import failed: {e}")

    b = _bundle()
    ids = b.kurier_ids
    names = list(ids.keys()) + list(b.grafik_full_names.keys())
    for name in names:
        assert _n(legacy_worker(name, ids)) == _n(normalize.resolve_worker(name, ids)), name

    try:
        from dispatch_v2 import panel_roster as pr
    except Exception:  # pragma: no cover
        return  # worker parity already proven
    roster = registry.build_registry(b)._roster
    for name in names:
        m = pr.match_name_to_cid(name, roster)
        legacy_p = _n(m.cid) if m.status == "matched" else None
        mine = normalize.resolve_panel_roster(name, roster)
        mine_p = _n(mine.cid) if mine.status == "matched" else None
        assert legacy_p == mine_p, name
