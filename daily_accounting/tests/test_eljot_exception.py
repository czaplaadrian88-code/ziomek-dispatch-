"""Tests — Bar Eljot exception: H = suma_pobran_total - eljot_pobrania + eljot_cena."""
from dispatch_v2.daily_accounting.panel_scraper import (
    compute_h,
    parse_main_page,
    parse_eljot_page,
    build_courier_iteration_list,
    _parse_zl,
)


def test_adrian_cit_23_04_sanity():
    # Spec G1: 1051.30 - 57.00 + 20.00 = 1014.30
    assert compute_h(1051.30, 57.00, 20.00) == 1014.30


def test_no_eljot_orders():
    # brak zleceń Eljot → eljot_pobrania=0, eljot_cena=0 → H = suma_pobran_total
    assert compute_h(800.00, 0.0, 0.0) == 800.00


def test_eljot_cena_dynamic():
    # cena 25.00 zamiast 20.00 (scenario: cennik się zmienił)
    assert compute_h(1051.30, 57.00, 25.00) == 1019.30


def test_multiple_eljot_orders():
    # 3 dowozy × 20 → eljot_cena 60.00, pobrania_eljot 150 (3 ordery po 50)
    assert compute_h(1200.00, 150.00, 60.00) == 1110.00


def test_parse_zl_formats():
    # PL 1 234,56 / US 1,234.56 / plain 87.00 / plain 343.96
    assert _parse_zl("1 234,56") == 1234.56
    assert _parse_zl("2.408,44") == 2408.44
    assert _parse_zl("1,234.56") == 1234.56
    assert _parse_zl("343.96") == 343.96
    assert _parse_zl("87,00") == 87.0
    assert _parse_zl("") == 0.0
    assert _parse_zl("0,00") == 0.0


def test_parse_main_page_full():
    html = """
    <html>
    <body>
    <div>Ilość zleceń: 24</div>
    <div>Suma pobrań: 1 051,30 zł</div>
    <div>Suma płatności kartą: 405,00 zł</div>
    </body></html>
    """
    out = parse_main_page(html)
    assert out["ilosc_zlecen"] == 24
    assert out["suma_pobran_total"] == 1051.30
    assert out["suma_platnosci_karta"] == 405.00


def test_parse_eljot_page_full():
    html = """
    <html><body>
    <div>Suma doręczonych przesyłek: 20,00 zł</div>
    <div>Suma pobrań: 57,00 zł</div>
    </body></html>
    """
    out = parse_eljot_page(html)
    assert out["eljot_pobrania"] == 57.0
    assert out["eljot_cena"] == 20.0


def test_parse_eljot_page_empty_defaults():
    # Panel nie renderuje sekcji gdy 0 zleceń Eljot
    out = parse_eljot_page("<html><body>no data</body></html>")
    assert out["eljot_pobrania"] == 0.0
    assert out["eljot_cena"] == 0.0


def test_build_courier_iteration_list_dedupe():
    # cid=500 ma 3 aliasy, cid=522 ma 2 — canonical = pierwszy
    kids = {
        "Adrian Cit": 457,
        "Grzegorz": 500,
        "Grzegorz R": 500,
        "Grzegorz Rogowski": 500,
        "Szymon Sa": 522,
        "Szymon Sadowski": 522,
        "Krystian": 61,
        "Koordynator": 26,
    }
    excluded = {23, 26, 61}
    out = build_courier_iteration_list(kids, excluded)
    out_map = dict(out)
    # Krystian 61 + Koordynator 26 wykluczeni
    assert 61 not in out_map
    assert 26 not in out_map
    # canonical alias dla cid=500 = pierwszy (Grzegorz)
    assert out_map[500] == "Grzegorz"
    # canonical alias dla cid=522 = pierwszy (Szymon Sa)
    assert out_map[522] == "Szymon Sa"
    # Adrian Cit normalny
    assert out_map[457] == "Adrian Cit"
    # Total unique cids
    assert len(out) == 3


def test_build_courier_iteration_list_empty_excluded():
    kids = {"A": 1, "B": 2}
    out = build_courier_iteration_list(kids, set())
    assert dict(out) == {1: "A", 2: "B"}
