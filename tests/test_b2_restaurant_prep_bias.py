"""SP-B2-PREPBIAS — testy generatora tools/restaurant_prep_bias.py (2026-06-11).

Format wyjścia = kontrakt sesji A; zgodność weryfikowana KONSUMENTEM
calib_maps.prep_bias_for. Pułapki CSV: BOM, multiline uwagi, HH:MM bez daty
(rollover za północ), dedup po zid między plikami.
"""
import json
from datetime import datetime, timedelta, timezone

from dispatch_v2 import calib_maps
from dispatch_v2.tools import restaurant_prep_bias as rpb

HDR = ("nr zlecenia,data złożenia zlecenia,nazwa restauracji,odbiorca,"
       "miejscowość docelowa,pobranie,cena za transport,czas restauracji,"
       "czas kuriera,czas odbioru,czas doręczenia,status,kurier,"
       "oczekiwanie odbiór,uwagi")


def _row(zid, created, rest, t_rest, t_odbior, status="doręczone", uwagi=""):
    uw = '"' + uwagi.replace('"', '""') + '"' if uwagi else ""
    return (f'{zid},{created},{rest},"Klient 1 tel.111",Białystok,0.00,20.00,'
            f"{t_rest},12:00,{t_odbior},13:00,{status},Kurier X,00:00:00,{uw}")


def _write_csv(path, rows, bom=True):
    content = HDR + "\r\n" + "\r\n".join(rows) + "\r\n"
    data = content.encode("utf-8")
    if bom:
        data = b"\xef\xbb\xbf" + data
    path.write_bytes(data)


def _fresh(days_ago=1, hour=12):
    return (datetime.now() - timedelta(days=days_ago)).replace(
        hour=hour, minute=0, second=0, microsecond=0
    ).strftime("%Y-%m-%d %H:%M:%S")


def test_combine_hhmm_rollover_midnight():
    created = datetime(2026, 6, 10, 23, 30, 0)
    # zdarzenie 00:15 = już następnego dnia (HH:MM bez daty!)
    got = rpb._combine_hhmm(created, "00:15")
    assert got == datetime(2026, 6, 11, 0, 15)
    # zdarzenie 23:45 = ten sam dzień
    assert rpb._combine_hhmm(created, "23:45") == datetime(2026, 6, 10, 23, 45)
    assert rpb._combine_hhmm(created, "") is None
    assert rpb._combine_hhmm(created, "zł") is None


def test_read_csv_bom_multiline_dedupe(tmp_path, monkeypatch):
    big = tmp_path / "big.csv"
    inc = tmp_path / "inc.csv"
    created = _fresh(days_ago=2, hour=12)
    rows_big = [
        _row(100, created, "Mama Thai", "12:30", "12:45",
             uwagi="linia1\nlinia2 z, przecinkiem\nlinia3"),  # multiline uwagi
        _row(101, created, "Mama Thai", "12:30", "12:40"),
        _row(102, created, "Mama Thai", "12:30", "12:50", status="anulowane"),
    ]
    rows_inc = [
        _row(100, created, "Mama Thai", "12:30", "12:59"),  # duplikat zid=100
        _row(103, created, "Raj", "12:00", "12:05"),
    ]
    _write_csv(big, rows_big)
    _write_csv(inc, rows_inc)
    # inc świeższy mtime → wygrywa dedup
    import os, time
    os.utime(big, (time.time() - 100, time.time() - 100))
    monkeypatch.setattr(rpb, "CSV_GLOB", str(tmp_path / "*.csv"))

    obs, stats = rpb.read_csv_observations(days=30)
    # zid=100 z inc (bias 29), zid=101 (10), zid=103 (5); anulowane odpada
    biases = sorted(b for _, _, b in obs)
    assert biases == [5.0, 10.0, 29.0]
    rests = {r for r, _, _ in obs}
    assert rests == {"mama thai", "raj"}


def test_read_csv_skips_foreign_header(tmp_path, monkeypatch):
    alien = tmp_path / "alien.csv"
    alien.write_text("kolumna_a,kolumna_b\n1,2\n", encoding="utf-8")
    ok = tmp_path / "ok.csv"
    _write_csv(ok, [_row(1, _fresh(), "Raj", "12:00", "12:08")])
    monkeypatch.setattr(rpb, "CSV_GLOB", str(tmp_path / "*.csv"))
    obs, stats = rpb.read_csv_observations(days=30)
    assert len(obs) == 1
    assert any("SKIP" in v for v in stats.values())


def test_window_and_sanity_filters(tmp_path, monkeypatch):
    f = tmp_path / "f.csv"
    _write_csv(f, [
        _row(1, _fresh(days_ago=200), "Raj", "12:00", "12:10"),  # poza oknem
        _row(2, _fresh(), "Raj", "12:00", "11:30"),              # bias -30 OK
        _row(3, _fresh(), "Raj", "18:00", "12:00"),              # rollover → +18h=fail sanity? (12:00 nast. dnia = +1080) → odpada
        _row(4, _fresh(), "Raj", "12:00", "12:10"),              # OK +10
    ])
    monkeypatch.setattr(rpb, "CSV_GLOB", str(tmp_path / "*.csv"))
    obs, _ = rpb.read_csv_observations(days=30)
    biases = sorted(b for _, _, b in obs)
    assert biases == [-30.0, 10.0]


def test_build_table_min_n_and_slots():
    obs = ([("mama thai", "peak_lunch", 12.0)] * 35
           + [("mama thai", "high_risk", 20.0)] * 5
           + [("raj", "peak_lunch", 2.0)] * 10)
    table = rpb.build_table(obs)
    # mama thai: peak_lunch n=35 OK; high_risk n=5 < 30 → brak; all n=40 OK
    mt = table["restaurants"]["mama thai"]
    assert set(mt) == {"peak_lunch", "all"}
    assert mt["peak_lunch"]["bias_med"] == 12.0 and mt["peak_lunch"]["n"] == 35
    assert mt["all"]["n"] == 40
    # raj n=10 → żadnej komórki → brak wpisu
    assert "raj" not in table["restaurants"]
    # global: peak_lunch 45 obs, all 50
    assert table["global"]["peak_lunch"]["n"] == 45
    assert table["global"]["all"]["n"] == 50


def test_run_output_consumed_by_calib_maps(tmp_path, monkeypatch):
    """E2E zgodności z kontraktem: plik czyta calib_maps.prep_bias_for."""
    f = tmp_path / "hist.csv"
    created = _fresh(days_ago=1, hour=12)  # 12:00 → slot peak_lunch
    _write_csv(f, [_row(i, created, "Pizzeria 105", "12:30", "12:42")
                   for i in range(40)])
    out = tmp_path / "restaurant_prep_bias.json"
    monkeypatch.setattr(rpb, "CSV_GLOB", str(tmp_path / "*.csv"))
    monkeypatch.setattr(rpb, "VIOLATIONS_LOG", str(tmp_path / "brak.jsonl"))
    monkeypatch.setattr(rpb, "OUT_PATH", str(out))
    rpb.run(days=30, dry_run=False)

    monkeypatch.setattr(calib_maps, "PREP_BIAS_MAP_PATH", str(out))
    calib_maps.reset_caches()
    try:
        noon = datetime(2026, 6, 10, 10, 30, tzinfo=timezone.utc)  # 12:30 Warsaw
        # konsument normalizuje nazwę strip().lower()
        assert calib_maps.prep_bias_for("  Pizzeria 105 ", now=noon) == 12.0
        # nieznana restauracja → global (peak_lunch albo all)
        assert calib_maps.prep_bias_for("Nieznana", now=noon) == 12.0
    finally:
        calib_maps.reset_caches()


def test_run_dry_run_no_write(tmp_path, monkeypatch):
    monkeypatch.setattr(rpb, "CSV_GLOB", str(tmp_path / "*.csv"))
    monkeypatch.setattr(rpb, "VIOLATIONS_LOG", str(tmp_path / "brak.jsonl"))
    out = tmp_path / "out.json"
    monkeypatch.setattr(rpb, "OUT_PATH", str(out))
    payload = rpb.run(days=30, dry_run=True)
    assert not out.exists()
    assert payload["version"] == 1


def test_violations_overlay_window(tmp_path, monkeypatch):
    log = tmp_path / "restaurant_violations.jsonl"
    now = datetime.now(timezone.utc)
    recs = [
        {"ts": (now - timedelta(days=2)).isoformat(), "restaurant": "Raj", "wait_min": 8.0},
        {"ts": (now - timedelta(days=2)).isoformat(), "restaurant": "Raj", "wait_min": 12.0},
        {"ts": (now - timedelta(days=60)).isoformat(), "restaurant": "Raj", "wait_min": 99.0},
    ]
    log.write_text("\n".join(json.dumps(r) for r in recs) + "\n")
    monkeypatch.setattr(rpb, "VIOLATIONS_LOG", str(log))
    ov = rpb.recent_violations_overlay(14)
    assert ov == {"raj": {"n": 2, "wait_med": 10.0}}
