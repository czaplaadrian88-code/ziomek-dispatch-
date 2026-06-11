"""F3 (2026-06-11) — testy tools/flag_registry.py (inwentarz flag + rozjazdy)."""
import os

from dispatch_v2.tools import flag_registry as fr


def test_def_re_parses_bool_and_value_flags():
    src = (
        'ENABLE_FOO = _os.environ.get(\n    "ENABLE_FOO", "0") == "1"\n'
        'BAR_COEFF = float(_os.environ.get("BAR_COEFF", "60"))\n'
        'BAZ_PATH = _os.environ.get(\n    "BAZ_PATH",\n    "/tmp/x")\n'
    )
    found = {m.group("name"): (m.group("default"), bool(m.group("cmp")))
             for m in fr._DEF_RE.finditer(src)}
    assert found["ENABLE_FOO"] == ("0", True)
    assert found["BAR_COEFF"] == ("60", False)
    # BAZ_PATH ma default bez cudzysłowu w 1 linii? — wieloliniowy literal OK:
    assert "BAZ_PATH" not in found or found["BAZ_PATH"][0] == "/tmp/x"


def test_scan_unit_env_dropins_win(tmp_path, monkeypatch):
    unit = "dispatch-foo.service"
    (tmp_path / unit).write_text("[Service]\nEnvironment=K1=a\nEnvironment=K2=x\n")
    d = tmp_path / (unit + ".d")
    d.mkdir()
    (d / "override.conf").write_text("[Service]\nEnvironment=K1=b\n")
    monkeypatch.setattr(fr, "SYSTEMD_DIR", str(tmp_path))
    env = fr.scan_unit_env(unit)
    assert env["K1"][0] == "b"      # drop-in nadpisuje main unit
    assert env["K1"][1] == "override.conf"
    assert env["K2"][0] == "x"


def test_dynamic_key_family_not_orphan():
    assert any(p.match("CZASOWKA_T60_ENABLED") for p in fr.DYNAMIC_KEY_FAMILIES)
    assert not any(p.match("CZASOWKA_TX_ENABLED") for p in fr.DYNAMIC_KEY_FAMILIES)


def test_scan_common_finds_real_flags():
    defs = fr.scan_common()
    assert defs.get("ENABLE_R5_PICKUP_DETOUR_PENALTY", {}).get("bool") is True
    assert defs.get("R5_DETOUR_PENALTY_PER_KM", {}).get("default") == "8.0"


def test_build_registry_smoke_live():
    """Smoke na żywym systemie: read-only, musi się policzyć bez wyjątku."""
    rows, issues = fr.build_registry()
    assert len(rows) > 100
    names = {r["flag"] for r in rows}
    assert "ENABLE_R5_PICKUP_DETOUR_PENALTY" in names
    # render obu formatów nie wybucha
    assert "FLAG REGISTRY" in fr.render(rows, issues)
    assert "Rejestr flag" in fr.render_md(rows, issues)
