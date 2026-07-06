"""K07 (program refaktoru, 2026-07-06) — pre-proposal recheck PRZED pulą.

Kontrakt: zero HTTP w ocenie kandydata. Prefetch = JEDNO wywołanie istniejącej
get_fresh_czas_kuriera_for_bag na UNII worków floty (worki rozłączne per kurier
→ ten sam zbiór zleceń co legacy); w pętli wyłącznie czysta aplikacja
(_k07_apply_fresh_ck — wspólna reguła nadpisania dla OBU ścieżek, kontrakt ①).
Gate ENABLE_PRE_RECHECK_BEFORE_POOL (ETAP4, brak klucza=OFF=None=legacy 1:1).
"""
from datetime import datetime, timezone
from types import SimpleNamespace

import dispatch_v2.dispatch_pipeline as dp

NOW = datetime(2026, 7, 6, 13, 0, tzinfo=timezone.utc)
_BB = {"pickup_coords": (53.13, 23.16), "delivery_coords": (53.12, 23.15),
       "status": "assigned", "restaurant": "Testownia"}


def _bag_entry(oid, ck="2026-07-06T14:30:00+02:00"):
    return dict(_BB, order_id=oid, czas_kuriera_warsaw=ck,
                assigned_at="2026-07-06T11:00:00+00:00", courier_id="111")


def _fleet(*bags):
    return {str(i): SimpleNamespace(bag=list(b)) for i, b in enumerate(bags)}


# ---------- gate ----------

def test_off_zwraca_none_i_nie_fetchuje(monkeypatch):
    calls = []
    monkeypatch.setattr(dp, "get_fresh_czas_kuriera_for_bag",
                        lambda sims, now: calls.append(1) or {})
    # brak klucza w (stripowanym) flags.json + const False = OFF
    out = dp._k07_prefetch_fresh_ck(_fleet([_bag_entry("1")]), NOW)
    assert out is None and calls == []


def test_on_unia_workow_dedup_jedno_wywolanie(monkeypatch):
    monkeypatch.setattr(dp.C, "decision_flag",
                        lambda n: n == "ENABLE_PRE_RECHECK_BEFORE_POOL")
    monkeypatch.setattr(dp.C, "ENABLE_V327_PRE_PROPOSAL_RECHECK", True)
    seen = []

    def fake_fetch(sims, now):
        seen.append(sorted(s.order_id for s in sims))
        return {s.order_id: "2026-07-06T15:00:00+02:00" for s in sims}

    monkeypatch.setattr(dp, "get_fresh_czas_kuriera_for_bag", fake_fetch)
    fleet = _fleet([_bag_entry("1"), _bag_entry("2")],
                   [_bag_entry("3"), _bag_entry("1")])  # duplikat "1" → dedup
    out = dp._k07_prefetch_fresh_ck(fleet, NOW)
    assert len(seen) == 1, "dokładnie JEDEN fetch na decyzję"
    assert seen[0] == ["1", "2", "3"], "unia worków z dedupem"
    assert out == {"1": "2026-07-06T15:00:00+02:00",
                   "2": "2026-07-06T15:00:00+02:00",
                   "3": "2026-07-06T15:00:00+02:00"}


def test_on_ale_v327_off_nic_do_prefetchu(monkeypatch):
    monkeypatch.setattr(dp.C, "decision_flag",
                        lambda n: n == "ENABLE_PRE_RECHECK_BEFORE_POOL")
    monkeypatch.setattr(dp.C, "ENABLE_V327_PRE_PROPOSAL_RECHECK", False)
    calls = []
    monkeypatch.setattr(dp, "get_fresh_czas_kuriera_for_bag",
                        lambda sims, now: calls.append(1) or {})
    assert dp._k07_prefetch_fresh_ck(_fleet([_bag_entry("1")]), NOW) is None
    assert calls == []


def test_fail_soft_na_wyjatku_fetcha(monkeypatch):
    monkeypatch.setattr(dp.C, "decision_flag",
                        lambda n: n == "ENABLE_PRE_RECHECK_BEFORE_POOL")
    monkeypatch.setattr(dp.C, "ENABLE_V327_PRE_PROPOSAL_RECHECK", True)
    monkeypatch.setattr(dp, "get_fresh_czas_kuriera_for_bag",
                        lambda sims, now: (_ for _ in ()).throw(OSError("panel down")))
    out = dp._k07_prefetch_fresh_ck(_fleet([_bag_entry("1")]), NOW)
    assert out is None, "awaria prefetchu = None = pętla idzie ścieżką legacy"


def test_pusta_flota_zwraca_pusty_dict(monkeypatch):
    monkeypatch.setattr(dp.C, "decision_flag",
                        lambda n: n == "ENABLE_PRE_RECHECK_BEFORE_POOL")
    monkeypatch.setattr(dp.C, "ENABLE_V327_PRE_PROPOSAL_RECHECK", True)
    assert dp._k07_prefetch_fresh_ck(_fleet([]), NOW) == {}


# ---------- wspólna reguła aplikacji (kontrakt ①, 1:1 z legacy) ----------

def test_apply_regula_nadpisania_identyczna_z_legacy():
    o1 = SimpleNamespace(order_id="1", czas_kuriera_warsaw="A")
    o2 = SimpleNamespace(order_id="2", czas_kuriera_warsaw="B")
    o3 = SimpleNamespace(order_id="3", czas_kuriera_warsaw="C")
    dp._k07_apply_fresh_ck([o1, o2, o3], {"1": "A",      # równy → bez zmiany
                                          "2": "NOWY",   # różny → nadpisz
                                          "3": None})    # None → bez zmiany
    assert (o1.czas_kuriera_warsaw, o2.czas_kuriera_warsaw, o3.czas_kuriera_warsaw) \
        == ("A", "NOWY", "C")


def test_apply_odporna_na_puste():
    dp._k07_apply_fresh_ck([], {"1": "X"})
    dp._k07_apply_fresh_ck(None, {"1": "X"})
    dp._k07_apply_fresh_ck([SimpleNamespace(order_id="1", czas_kuriera_warsaw="A")], None)
