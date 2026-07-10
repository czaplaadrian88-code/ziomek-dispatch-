"""Z-P1-05 Faza A — collision/gap validators (hermetic; fixtures only)."""
import json
from pathlib import Path

from dispatch_v2.identity import collisions, registry, sources

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


def _git_map():
    return json.loads((FIX / "daily_kurier_full_names_git.json").read_text())


# --- (a) normalized alias -> more than one CID ----------------------------- #


def test_alias_multi_cid():
    rows = collisions.find_alias_multi_cid(_bundle().kurier_ids)
    assert len(rows) == 1
    row = rows[0]
    assert row["norm_alias"] == "anna ko"          # "Anna Ko." vs "Anna Ko"
    assert row["cids"] == ["300", "301"]


# --- (b) bare-key poison, COMPUTED (not hardcoded) ------------------------- #


def test_bare_key_poison_computed_from_single_word_keys():
    rows = collisions.find_bare_key_poison(_bundle().kurier_ids)
    aliases = {r["alias"] for r in rows}
    assert aliases == {"Adam", "Koordynator", "Marek", "Piotr"}
    # the set is derived from single-word kurier_ids keys, never hardcoded
    for r in rows:
        assert " " not in r["alias"].strip()


# --- (c) full-name divergence with abbrev-vs-full sieve -------------------- #


def test_names_compatible_sieve():
    # different first name -> real conflict (Kuba vs Jakub, cid 370 class)
    assert collisions.names_compatible("Kuba Olchowski", "Jakub Olchowski") is False
    # abbreviation of the same surname -> same person, sieved out
    assert collisions.names_compatible("Jakub Ol", "Jakub Olchowski") is True
    # ascii abbrev vs diacritic surname -> conflict (cid 376 class, no folding)
    assert collisions.names_compatible("Paweł Sc", "Paweł Ściepko") is False
    # extension of the same stem -> compatible
    assert collisions.names_compatible("Adam Nowak", "Adam Nowakowski") is True


def test_fullname_divergence_flags_only_real_conflicts():
    rows = collisions.find_fullname_divergence(_bundle())
    flagged = {r["cid"] for r in rows}
    assert flagged == {"200", "210"}   # Kuba/Jakub + diacritic; NOT 100 (Nowak/Nowakowski)


# --- (d) missing courier_names / tier -------------------------------------- #


def test_missing_names_and_tiers():
    b = _bundle()
    all_cids = [r.cid for r in registry.build_registry(b).all_records()]
    gaps = collisions.find_missing_names_and_tiers(b, all_cids)
    assert gaps["missing_courier_names"] == ["220"]   # Tomasz absent from courier_names
    assert gaps["missing_tier"] == ["230"]            # Robert absent from courier_tiers
    # coordinator cid 26 is legitimately absent and must NOT be flagged
    assert "26" not in gaps["missing_courier_names"]
    assert "26" not in gaps["missing_tier"]


# --- (e) duplicate / orphan PINs (pin_last2 only, never plaintext) --------- #


def test_duplicate_and_orphan_pins():
    b = _bundle()
    dp = collisions.find_duplicate_pins(b.kurier_piny, b.kurier_ids)
    multi = {d["alias"]: d for d in dp["multi_pin_aliases"]}
    assert "Adam No" in multi
    assert sorted(multi["Adam No"]["pin_last2"]) == ["00", "01"]
    orphans = {d["alias"] for d in dp["orphan_pins"]}
    assert "Ghost Xy" in orphans
    # never leak a full PIN
    for d in dp["multi_pin_aliases"]:
        for p in d["pin_last2"]:
            assert len(p) <= 2
    for d in dp["orphan_pins"]:
        assert len(d["pin_last2"]) <= 2


# --- (f) git-vs-live divergence -------------------------------------------- #


def test_git_live_divergence():
    b = _bundle()
    div = collisions.find_git_live_divergence(_git_map(), b.daily_full_names)
    assert set(div["added"]) == {"Tomasz Zi"}
    assert set(div["removed"]) == {"Robert Ma"}
    assert set(div["changed"]) == {"Adam No"}
    assert div["changed"]["Adam No"] == {"git": "Adam Nowak", "live": "Adam Nowakowski"}


# --- end to end ------------------------------------------------------------ #


def test_run_collisions_summary_end_to_end():
    b = _bundle()
    all_cids = [r.cid for r in registry.build_registry(b).all_records()]
    rep = collisions.run_collisions(b, all_cids=all_cids, git_full_names=_git_map())
    s = rep.summary()
    assert s["alias_multi_cid"] == 1
    assert s["bare_key_poison"] == 4
    assert s["fullname_divergence"] == 2
    assert s["missing_courier_names"] == 1
    assert s["missing_tier"] == 1
    assert s["duplicate_pin_aliases"] == 1
    assert s["orphan_pins"] == 1
    assert s["git_live_added"] == 1
    assert s["git_live_removed"] == 1
    assert s["git_live_changed"] == 1
    # to_dict must be JSON-serializable (report/handoff)
    json.dumps(rep.to_dict())
