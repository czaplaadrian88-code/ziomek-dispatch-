"""restaurant_mapper: rebuild bez zmiany mapowania NIE nadpisuje pliku.

Kontekst (2026-06-13): restaurant_company_mapping.json jest trackowany w git;
cotygodniowy build_and_save bumpował sam generated_at i trzymał repo wiecznie
brudnym. Fix: _content_unchanged + skip zapisu, plik na dysku = źródło prawdy.
"""
import json

import pytest

from dispatch_v2.cod_weekly import restaurant_mapper as rm


def _fake_payload_parts():
    mapping = {"Restauracja A": 101, "Restauracja B": [102, 103]}
    return {
        "mapping": mapping,
        "unmatched": [],
        "unused_panel": ["Firma C"],
        "method_per_entry": {"Restauracja A": "strict", "Restauracja B": "alias"},
    }


@pytest.fixture
def patched_build(monkeypatch, tmp_path):
    """build_and_save z zamockowanym scrape/sheet/match i MAPPING_PATH w tmp."""
    parts = _fake_payload_parts()
    monkeypatch.setattr(rm, "scrape_panel_dropdown", lambda: {"x": 1})
    monkeypatch.setattr(rm, "fetch_sheet_restaurants", lambda: ["Restauracja A", "Restauracja B"])
    monkeypatch.setattr(rm, "match_restaurants", lambda rows, panel: dict(parts))
    path = tmp_path / "restaurant_company_mapping.json"
    monkeypatch.setattr(rm, "MAPPING_PATH", path)
    return path, parts


def test_content_unchanged_ignores_generated_at():
    a = {"mapping": {"X": 1}, "generated_at": "2026-06-01T08:00:00"}
    b = {"mapping": {"X": 1}, "generated_at": "2026-06-09T07:55:00"}
    assert rm._content_unchanged(a, b) is True


def test_content_unchanged_detects_real_change():
    a = {"mapping": {"X": 1}, "generated_at": "t1"}
    b = {"mapping": {"X": 2}, "generated_at": "t2"}
    assert rm._content_unchanged(a, b) is False


def test_content_unchanged_empty_existing_writes():
    assert rm._content_unchanged({}, {"mapping": {}}) is False
    assert rm._content_unchanged(None, {"mapping": {}}) is False


def test_build_skips_write_when_mapping_identical(patched_build):
    path, _ = patched_build
    first = rm.build_and_save()
    assert path.exists()
    on_disk_1 = json.loads(path.read_text(encoding="utf-8"))
    mtime_1 = path.stat().st_mtime_ns

    second = rm.build_and_save()
    on_disk_2 = json.loads(path.read_text(encoding="utf-8"))
    assert path.stat().st_mtime_ns == mtime_1, "plik nadpisany mimo braku zmian"
    assert on_disk_2["generated_at"] == on_disk_1["generated_at"]
    # zwrotka = stan z dysku (mapping dalej kompletny dla run_weekly)
    assert second["mapping"] == first["mapping"]
    assert "counts" in second


def test_build_writes_when_mapping_changed(patched_build, monkeypatch):
    path, parts = patched_build
    rm.build_and_save()
    gen_1 = json.loads(path.read_text(encoding="utf-8"))["generated_at"]

    changed = dict(parts)
    changed["mapping"] = {"Restauracja A": 999}
    monkeypatch.setattr(rm, "match_restaurants", lambda rows, panel: changed)
    out = rm.build_and_save()
    on_disk = json.loads(path.read_text(encoding="utf-8"))
    assert on_disk["mapping"] == {"Restauracja A": 999}
    assert on_disk["generated_at"] != gen_1 or out["mapping"] == {"Restauracja A": 999}


def test_build_writes_when_file_missing(patched_build):
    path, _ = patched_build
    assert not path.exists()
    out = rm.build_and_save()
    assert path.exists()
    assert out["mapping"] == _fake_payload_parts()["mapping"]
