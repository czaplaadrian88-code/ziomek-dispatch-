"""F3 (2026-06-11) — testy tools/flag_registry.py (inwentarz flag + rozjazdy)."""
import json
import os

from dispatch_v2.tools import flag_registry as fr


def _synthetic_registry(tmp_path, monkeypatch):
    """Hermetyczny snapshot post-migration: flags.json wygrywa nad starym env.

    Globalne ścieżki narzędzia celowo kierujemy w nieistniejące miejsca. Jeżeli
    build_registry zignoruje jawne argumenty, test ma się wywrócić zamiast
    przeczytać live hosta.
    """
    common_py = tmp_path / "common.py"
    flags_json = tmp_path / "flags.json"
    systemd_dir = tmp_path / "systemd"
    systemd_dir.mkdir()
    common_py.write_text(
        'import os\n'
        'ETAP4_DECISION_FLAGS = ("USE_V2_PARSER",)\n'
        '_FINGERPRINT_EXTRA_FLAGS = ()\n'
        'FLAGS_JSON_NUMERIC_OVERRIDES = ()\n'
        'USE_V2_PARSER = False\n'
        'SYNTH_SCOPED = os.environ.get("SYNTH_SCOPED", "0") == "1"\n',
        encoding="utf-8",
    )
    flags_json.write_text(json.dumps({"USE_V2_PARSER": True}), encoding="utf-8")
    (systemd_dir / "dispatch-panel-watcher.service").write_text(
        "[Service]\nEnvironment=USE_V2_PARSER=1\n", encoding="utf-8"
    )
    (systemd_dir / "dispatch-plan-recheck.service").write_text(
        "[Service]\nEnvironment=SYNTH_SCOPED=1\n", encoding="utf-8"
    )
    monkeypatch.setattr(fr, "FLAGS_JSON", str(tmp_path / "DO_NOT_READ_LIVE_FLAGS"))
    monkeypatch.setattr(fr, "SYSTEMD_DIR", str(tmp_path / "DO_NOT_READ_LIVE_SYSTEMD"))
    monkeypatch.setattr(
        fr,
        "SERVICE_SCOPED",
        {**fr.SERVICE_SCOPED,
         "SYNTH_SCOPED": ("dispatch-plan-recheck.service", "syntetyczny owner testu")},
    )
    rows, issues = fr.build_registry(
        common_path=str(common_py),
        flags_path=str(flags_json),
        systemd_dir=str(systemd_dir),
        code_roots=(str(tmp_path),),
    )
    return rows, issues, common_py, flags_json, systemd_dir


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


def test_completeness_no_gaps_synthetic(tmp_path, monkeypatch):
    """STRAŻNIK REGRESJI (spec L0.1): KAŻDA flaga decyzyjna/numeryczna ETAP4 i
    KAŻDY klucz flags.json MUSI mieć wiersz. Nowa flaga bez definicji łapanej
    skanerem → gap>0 = FAIL. To dowód pełnego skanu (names NIE seedowane listą)."""
    rows, _issues, common_py, flags_json, _systemd_dir = _synthetic_registry(
        tmp_path, monkeypatch
    )
    gaps = fr.completeness_gaps(
        rows, common_path=str(common_py), flags_path=str(flags_json)
    )
    assert gaps == [], f"BRAKI POKRYCIA rejestru (regresja skanera): {gaps}"


def test_classification_synthetic_no_unclassified_divergence(tmp_path, monkeypatch):
    """Każdy env-frozen-subset rozjazd MA werdykt (scoped/known/intentional) —
    zero 'open' nieklasyfikowanych. Nowa flaga env-only bez wpisu → FAIL."""
    _rows, issues, *_ = _synthetic_registry(tmp_path, monkeypatch)
    unclassified = fr.unclassified_divergences(issues)
    assert unclassified == [], (
        "Nieklasyfikowany env-frozen-subset (dopisz do SERVICE_SCOPED/KNOWN_DIVERGENCES): "
        + ", ".join(i["flag"] for i in unclassified))


def test_post_migration_json_overrides_env_is_open(tmp_path, monkeypatch):
    """Po migracji ETAP4 pozostały env-carrier jest jawnym długiem `open`.

    Historyczne `known-open` oznaczało brak kanonu cross-service. Po flipie
    kanonem jest flags.json, więc stary carrier ma być widoczny jako
    `json-overrides-env`, nie maskowany wpisem KNOWN_DIVERGENCES.
    """
    _rows, issues, common_py, flags_json, systemd_dir = _synthetic_registry(
        tmp_path, monkeypatch
    )
    opn = {id(i) for i in fr.open_issues(issues)}
    acc = {id(i) for i in fr.accepted_issues(issues)}
    for i in issues:
        assert (id(i) in opn) ^ (id(i) in acc), f"{i['flag']} nie sklasyfikowany rozłącznie"
    v2 = [i for i in issues if i["flag"] == "USE_V2_PARSER"]
    assert len(v2) == 1
    assert v2[0]["klass"] == "json-overrides-env"
    assert v2[0]["verdict"] == "open"
    assert id(v2[0]) in opn
    assert "USE_V2_PARSER" not in fr.KNOWN_DIVERGENCES
    rendered = fr.render(
        _rows,
        issues,
        common_path=str(common_py),
        flags_path=str(flags_json),
        systemd_dir=str(systemd_dir),
    )
    assert f"common={common_py}" in rendered
    assert f"flags={flags_json}" in rendered
    assert f"systemd={systemd_dir}" in rendered


def test_metric4_rozjazdy_count_is_open_only(tmp_path, monkeypatch):
    """Metryka #4 entropy_dashboard parsuje `ROZJAZDY (N)` = OTWARTE (open+known),
    NIE wszystkie issues. accepted-scoped/intentional poza licznikiem."""
    import re as _re
    _rows, issues, common_py, flags_json, systemd_dir = _synthetic_registry(
        tmp_path, monkeypatch
    )
    txt = fr.render(
        _rows,
        issues,
        common_path=str(common_py),
        flags_path=str(flags_json),
        systemd_dir=str(systemd_dir),
    )
    m = _re.search(r"ROZJAZDY \((\d+)\)", txt)
    assert m
    assert int(m.group(1)) == len(fr.open_issues(issues))
