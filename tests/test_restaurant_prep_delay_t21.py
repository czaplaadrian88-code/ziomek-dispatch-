"""T2.1 — generator mapy prep-delay per-restauracja z panelu CSV (advisory Tura 2).

Smoke: dedup po nr zlecenia, filtr mostów/test/anulowane, sygnały med/p90/shrunk,
wrap północy, stabilność. Read-only pomiar (zero decyzji).
"""
from __future__ import annotations

from dispatch_v2.tools import restaurant_prep_delay_build as B


_CSV_HEADER = ("nr zlecenia,data złożenia zlecenia,nazwa restauracji,odbiorca,"
               "miejscowość docelowa,pobranie,cena za transport,czas restauracji,"
               "czas kuriera,czas odbioru,czas doręczenia,status,kurier,"
               "oczekiwanie odbiór,uwagi")


def _row(oid, name, ck, co, cr, oczek, status="doręczone", date="2026-06-01"):
    return (f"{oid},{date} 12:00:00,{name},Adres,Białystok,0.00,20.0,"
            f"{cr},{ck},{co},13:00,{status},Kurier,{oczek},")


def _write(tmp_path, rows):
    p = tmp_path / "panel.csv"
    p.write_text(_CSV_HEADER + "\n" + "\n".join(rows) + "\n", encoding="utf-8")
    return str(p)


def test_wrap_and_parsers():
    assert B._hms("00:10:30") == 10.5
    assert B._hm("12:30") == 750
    assert B._wrap_diff(B._hm("00:15"), B._hm("23:50")) == 25  # przez północ
    assert B._wrap_diff(B._hm("12:40"), B._hm("12:30")) == 10


def test_dedup_and_exclude(tmp_path):
    rows = [
        _row(1, "Mama Thai Bistro", "12:30", "12:40", "12:25", "00:08:00"),
        _row(1, "Mama Thai Bistro", "12:30", "12:40", "12:25", "00:08:00"),  # dup oid
        _row(2, "Dr Tusz", "12:30", "12:31", "12:25", "00:00:00"),           # most → wykluczony
        _row(3, "Test", "12:30", "12:31", "12:25", "00:00:00"),              # test → wykluczony
        _row(4, "Grill Kebab", "12:30", "12:32", "12:28", "00:00:00", status="anulowane"),
    ]
    recs = B.load_records(_write(tmp_path, rows))
    names = {r["name"] for r in recs}
    assert names == {"Mama Thai Bistro"}  # dedup + filtr mostów/test/anulowane
    assert len(recs) == 1


def test_build_separates_slow_fast(tmp_path):
    rows = []
    for i in range(30):
        rows.append(_row(1000 + i, "Wolna Kuchnia", "12:30", "12:42", "12:25", "00:10:00"))
        rows.append(_row(2000 + i, "Szybka Kuchnia", "12:30", "12:31", "12:29", "00:00:00"))
    m = B.build(B.load_records(_write(tmp_path, rows)))
    slow = m["restaurants"]["Wolna Kuchnia"]
    fast = m["restaurants"]["Szybka Kuchnia"]
    assert slow["pickup_delay_med"] > fast["pickup_delay_med"]
    assert slow["oczek_p90"] > fast["oczek_p90"]
    assert slow["n"] == 30
