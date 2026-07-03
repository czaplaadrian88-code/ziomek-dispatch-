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


# ── L0.1 (2026-07-02): balanced-paren + literal scan + klasyfikacja + completeness ──

def test_def_re_matches_both_os_and_underscore_os():
    """Broadening L0.1: `os.environ.get` ORAZ `_os.environ.get` (34 defów w common.py
    używa non-underscore — regresja gubiła je → 4 BRAKI POKRYCIA)."""
    src = ('MIN_PROPOSE_SCORE = float(os.environ.get("MIN_PROPOSE_SCORE", "-100.0"))\n'
           'FOO = _os.environ.get("FOO", "0") == "1"\n')
    found = {m.group("name"): m.group("default") for m in fr._DEF_RE.finditer(src)}
    assert found.get("MIN_PROPOSE_SCORE") == "-100.0"   # os. (bez podkreślenia) łapane
    assert found.get("FOO") == "0"


def test_scan_decision_lists_balanced_over_comment_parens():
    """Balans nawiasów IGNORUJE `)` w komentarzu — naiwne `\\((.*?)\\)` ucinało
    krotkę na pierwszym `)` w komentarzu i gubiło większość flag."""
    src = (
        'ETAP4_DECISION_FLAGS = (\n'
        '    "ENABLE_A",   # komentarz z ) nawiasem w środku (celowo)\n'
        '    "ENABLE_B",   # kolejny (nawias) tu\n'
        '    "ENABLE_C",\n'
        ')\n'
        'FLAGS_JSON_NUMERIC_OVERRIDES = ("NUM_X",)\n'
    )
    body = fr._extract_paren_body(src, "ETAP4_DECISION_FLAGS")
    flags = tuple(__import__("re").findall(r'"([A-Z_][A-Z0-9_]*)"', body))
    assert flags == ("ENABLE_A", "ENABLE_B", "ENABLE_C")


def test_scan_literal_defaults_scoped_to_names():
    """Literal scanner łapie `= False/True/liczba` TYLKO dla podanych nazw
    (nie wciąga dowolnych stałych)."""
    import tempfile, os as _os
    src = (
        'ENABLE_X = False  # komentarz\n'
        'AUTO_CAP = 6\n'
        'CEIL = 90.0\n'
        'SOME_OTHER_CONST = True\n'          # NIE na liście → pominięta
        'ENABLE_ENV = _os.environ.get("ENABLE_ENV", "0") == "1"\n'
    )
    fd, p = tempfile.mkstemp(suffix=".py")
    _os.write(fd, src.encode()); _os.close(fd)
    try:
        out = fr.scan_literal_defaults(["ENABLE_X", "AUTO_CAP", "CEIL", "SOME_OTHER_CONST"], p)
    finally:
        _os.unlink(p)
    assert out["ENABLE_X"] == {"default": False, "bool": True}
    assert out["AUTO_CAP"] == {"default": 6, "bool": False}
    assert out["CEIL"] == {"default": 90.0, "bool": False}
    assert "SOME_OTHER_CONST" in out    # jest na liście → łapana (bool True)
    assert out["SOME_OTHER_CONST"]["default"] is True


def test_completeness_no_gaps_live():
    """STRAŻNIK REGRESJI (spec L0.1): KAŻDA flaga decyzyjna/numeryczna ETAP4 i
    KAŻDY klucz flags.json MUSI mieć wiersz. Nowa flaga bez definicji łapanej
    skanerem → gap>0 = FAIL. To dowód pełnego skanu (names NIE seedowane listą)."""
    rows, _issues = fr.build_registry()
    gaps = fr.completeness_gaps(rows)
    assert gaps == [], f"BRAKI POKRYCIA rejestru (regresja skanera): {gaps}"


def test_classification_live_no_unclassified_divergence():
    """Każdy env-frozen-subset rozjazd MA werdykt (scoped/known/intentional) —
    zero 'open' nieklasyfikowanych. Nowa flaga env-only bez wpisu → FAIL."""
    _rows, issues = fr.build_registry()
    unclassified = fr.unclassified_divergences(issues)
    assert unclassified == [], (
        "Nieklasyfikowany env-frozen-subset (dopisz do SERVICE_SCOPED/KNOWN_DIVERGENCES): "
        + ", ".join(i["flag"] for i in unclassified))


def test_open_and_accepted_partition_issues():
    """open_issues ∪ accepted_issues pokrywa wszystkie env-frozen-subset;
    known-open liczy się do OTWARTYCH (śledzone), scoped/intentional NIE."""
    _rows, issues = fr.build_registry()
    frozen = [i for i in issues if i["klass"] == "env-frozen-subset"]
    opn = {id(i) for i in fr.open_issues(issues)}
    acc = {id(i) for i in fr.accepted_issues(issues)}
    for i in frozen:
        assert (id(i) in opn) ^ (id(i) in acc), f"{i['flag']} nie sklasyfikowany rozłącznie"
    # USE_V2_PARSER = jedyny known-open (cross-service) — musi być w OTWARTYCH
    v2 = [i for i in issues if i["flag"] == "USE_V2_PARSER"]
    if v2:
        assert v2[0]["verdict"] == "known-open"
        assert id(v2[0]) in opn


def test_metric4_rozjazdy_count_is_open_only():
    """Metryka #4 entropy_dashboard parsuje `ROZJAZDY (N)` = OTWARTE (open+known),
    NIE wszystkie issues. accepted-scoped/intentional poza licznikiem."""
    import re as _re
    _rows, issues = fr.build_registry()
    txt = fr.render(_rows, issues)
    m = _re.search(r"ROZJAZDY \((\d+)\)", txt)
    assert m
    assert int(m.group(1)) == len(fr.open_issues(issues))
