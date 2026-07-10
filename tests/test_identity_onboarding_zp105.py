"""Z-P1-05 Faza A — onboarding/offboarding (hermetic; fixtures only).

The real atomic writer (courier_admin.add_new_courier) is never invoked for real
here — the apply path is exercised only against a spy, proving composition
without any write. Default is dry-run; --apply is env-gated.
"""
import json
from pathlib import Path

import pytest

from dispatch_v2.identity import onboarding, registry, sources

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


def _reg(b=None):
    return registry.build_registry(b or _bundle())


# --- dry-run plan ---------------------------------------------------------- #


def test_plan_onboard_dryrun_diff_five_files():
    b = _bundle()
    plan = onboarding.plan_onboard(_reg(b), b, "601", "Nowy Kandydat")
    assert plan["blocking"] == []
    assert plan["alias"] == "Nowy Ka"
    assert set(plan["diff"].keys()) == set(onboarding.ONBOARD_FILES)
    assert plan["diff"]["dispatch_state/kurier_ids.json"] == {"+Nowy Ka": "601", "+Nowy Kandydat": "601"}


def test_onboard_blocks_existing_cid():
    b = _bundle()
    plan = onboarding.plan_onboard(_reg(b), b, "100", "Adam Nowak")
    assert any("cid 100 already exists" in x for x in plan["blocking"])


def test_onboard_blocks_alias_collision():
    b = _bundle()
    # "Adam Nowicki" derives alias "Adam No" which already maps to cid 100
    plan = onboarding.plan_onboard(_reg(b), b, "601", "Adam Nowicki")
    assert any("Adam No" in x for x in plan["blocking"])


def test_onboard_bare_key_poison_warning():
    b = _bundle()
    # a new "Marek Zzz" is silently swallowed by the bare key "Marek" -> 110
    plan = onboarding.plan_onboard(_reg(b), b, "601", "Marek Zzz")
    assert plan["blocking"] == []
    assert any("110" in w for w in plan["warnings"])


# --- --apply gating (spy writer — no real write) --------------------------- #


def _patch_hermetic(monkeypatch, spy):
    b = _bundle()
    reg = _reg(b)
    monkeypatch.setattr(onboarding, "load_all", lambda *a, **k: b)
    monkeypatch.setattr(onboarding, "build_registry", lambda *a, **k: reg)
    import dispatch_v2.courier_admin as ca
    monkeypatch.setattr(ca, "add_new_courier", spy)


def test_apply_refused_without_env(monkeypatch, capsys):
    calls = []
    _patch_hermetic(monkeypatch, lambda *a, **k: calls.append(a) or {})
    monkeypatch.delenv("IDENTITY_ONBOARD_ALLOW", raising=False)
    rc = onboarding.main(["onboard", "--cid", "601", "--name", "Nowy Ktos", "--apply"])
    assert rc == 2
    assert calls == []                       # writer never touched
    assert "IDENTITY_ONBOARD_ALLOW=1" in capsys.readouterr().out


def test_apply_refused_on_blocking_even_with_env(monkeypatch, capsys):
    calls = []
    _patch_hermetic(monkeypatch, lambda *a, **k: calls.append(a) or {})
    monkeypatch.setenv("IDENTITY_ONBOARD_ALLOW", "1")
    rc = onboarding.main(["onboard", "--cid", "100", "--name", "Adam Nowak", "--apply"])
    assert rc == 3
    assert calls == []                       # blocked before any write
    assert "REFUSED" in capsys.readouterr().out


def test_apply_composes_writer_and_redacts_pin(monkeypatch, capsys):
    calls = []

    def spy(cid, name):
        calls.append((cid, name))
        return {"cid": cid, "alias": "Nowy Kt", "full_name": name, "pin": "1234"}

    _patch_hermetic(monkeypatch, spy)
    monkeypatch.setenv("IDENTITY_ONBOARD_ALLOW", "1")
    rc = onboarding.main(["onboard", "--cid", "601", "--name", "Nowy Ktos", "--apply"])
    out = capsys.readouterr().out
    assert rc == 0
    assert calls == [(601, "Nowy Ktos")]     # composed the real writer signature
    assert "1234" not in out                  # PIN redacted
    assert "pin_last2" in out and "34" in out


# --- offboard plan (no writes) --------------------------------------------- #


def test_offboard_plan_only():
    b = _bundle()
    plan = onboarding.plan_offboard(_reg(b), b, "240")
    assert plan["known"] is True
    assert "Marek Wolny" in plan["plan"]["shift_ignored_names.json += names"]
    assert plan["plan"]["registry: mark active=False"] is True
    assert "no file is written" in plan["note"]
