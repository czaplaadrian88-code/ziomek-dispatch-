#!/usr/bin/env python3
"""Testy filtra powiadomień reassignment-fwd (2026-06-25): pewna pozycja + cooldown.

Cel zmiany: zredukować wolumen powiadomień shadow-przerzutów (~170/dz → ~10-20/dz)
przez (1) filtr pewnej pozycji (A i B z GPS/last-known) i (2) cooldown per zlecenie.
KLUCZOWY invariant: filtr działa TYLKO na notify — _append_jsonl dostaje WSZYSTKIE
rekordy (eval 27.06 widzi pełny obraz).

Mock jak w test_reassignment_forward_shadow_2026_06_22: monkeypatch, zero sieci/OSRM.
"""
import json
import sys
import tempfile
from datetime import datetime, timezone, timedelta

sys.path.insert(0, "/root/.openclaw/workspace/scripts")

from dispatch_v2 import common as C
from dispatch_v2.tools import reassignment_forward_shadow as RFS

_N = datetime(2026, 6, 25, 18, 0, 0, tzinfo=timezone.utc)


def _row(oid, a_pos="gps", b_pos="gps", would=True, best="B"):
    return {"would_reassign": would, "order_id": oid, "best_cid": best,
            "a_pos_source": a_pos, "b_pos_source": b_pos}


# ============================ _pos_trusted ============================

def test_pos_trusted_matrix():
    assert RFS._pos_trusted("gps", "gps") is True
    assert RFS._pos_trusted("gps", "last_delivered") is True
    assert RFS._pos_trusted("last_known", "store") is True
    assert RFS._pos_trusted("gps", "pin") is False
    assert RFS._pos_trusted("pre_shift", "gps") is False
    assert RFS._pos_trusted("none", "none") is False
    assert RFS._pos_trusted(None, "gps") is False
    assert RFS._pos_trusted("", "gps") is False


# ============================ _notify_eligible: trusted filter ============================

def test_eligible_trusted_on_blocks_guessed():
    # trusted_only ON: gps/gps przechodzi, gps/pin odpada
    assert RFS._notify_eligible(_row("o1", "gps", "gps"), {}, _N, 20.0, True) is True
    assert RFS._notify_eligible(_row("o2", "gps", "pin"), {}, _N, 20.0, True) is False


def test_eligible_trusted_off_allows_guessed():
    # trusted_only OFF (flaga ON≠OFF): zgadnięta pozycja przechodzi
    assert RFS._notify_eligible(_row("o2", "gps", "pin"), {}, _N, 20.0, False) is True


def test_eligible_not_would_reassign_never():
    assert RFS._notify_eligible(_row("o1", "gps", "gps", would=False), {}, _N, 20.0, True) is False
    assert RFS._notify_eligible(_row("o1", "gps", "gps", would=False), {}, _N, 0.0, False) is False


# ============================ _notify_eligible: cooldown ============================

def test_eligible_cooldown_suppresses_then_allows():
    r = _row("o1", "gps", "gps")
    notif_recent = {"o1": {"best": "B", "ts": (_N - timedelta(minutes=5)).isoformat()}}
    notif_old = {"o1": {"best": "B", "ts": (_N - timedelta(minutes=25)).isoformat()}}
    assert RFS._notify_eligible(r, notif_recent, _N, 20.0, True) is False   # 5 < 20 → cisza
    assert RFS._notify_eligible(r, notif_old, _N, 20.0, True) is True       # 25 > 20 → znów


def test_eligible_cooldown_zero_disables():
    r = _row("o1", "gps", "gps")
    notif_recent = {"o1": {"best": "B", "ts": (_N - timedelta(minutes=1)).isoformat()}}
    # cooldown=0 → brak dławienia (filtr pozycji nadal działa)
    assert RFS._notify_eligible(r, notif_recent, _N, 0.0, True) is True


def test_eligible_cooldown_backward_compat_old_format():
    # stary notified format {oid: cid_int} (bez ts) → cooldown nie blokuje
    r = _row("o1", "gps", "gps")
    assert RFS._notify_eligible(r, {"o1": 179}, _N, 20.0, True) is True


# ============================ run_once: invariant jsonl pełny vs notify filtrowany ============================

def _patch_run_once(monkeypatch, captured, orders):
    """Podstaw zależności run_once: flagi ON, evaluate_order zwraca rekord per rec,
    _append_jsonl i _notify_telegram przechwytują wejście. ORDERS_STATE = temp plik."""
    tf = tempfile.NamedTemporaryFile("w", suffix=".json", delete=False, encoding="utf-8")
    json.dump({"orders": orders}, tf)
    tf.close()
    monkeypatch.setattr(RFS, "ORDERS_STATE", tf.name)
    monkeypatch.setattr(RFS.CR, "dispatchable_fleet", lambda: [])
    monkeypatch.setattr(C, "load_flags", lambda: {RFS.NOTIFY_COOLDOWN_KEY: 20.0})

    def fake_flag(name, default=False):
        return {RFS.FLAG: True, RFS.FLAG_TG: True,
                RFS.NOTIFY_TRUSTED_ONLY_FLAG: True}.get(name, default)
    monkeypatch.setattr(C, "flag", fake_flag)

    # evaluate_order: trusted dla oid kończącego się 't', guessed dla 'g'
    def fake_eval(rec, cid, fleet, now=None, margin=None):
        oid = str(rec.get("order_id"))
        trusted = oid.endswith("t")
        return _row(oid, "gps", "gps" if trusted else "pin")
    monkeypatch.setattr(RFS, "evaluate_order", fake_eval)

    monkeypatch.setattr(RFS, "_append_jsonl", lambda rows, path=RFS.OUT_JSONL: captured.__setitem__("jsonl", list(rows)))
    monkeypatch.setattr(RFS, "_load_notified", lambda: {})
    monkeypatch.setattr(RFS, "_save_notified", lambda d: captured.__setitem__("notified", dict(d)))

    def fake_notify(new_rows):
        captured["notified_rows"] = list(new_rows)
        return min(len(new_rows), RFS.TG_CAP)
    monkeypatch.setattr(RFS, "_notify_telegram", fake_notify)
    return tf.name


def _rec(oid, cid="9"):
    return {"order_id": oid, "courier_id": cid, "status": "assigned",
            "restaurant": "R", "pickup_coords": [53.13, 23.16], "delivery_coords": [53.14, 23.17]}


def test_run_once_jsonl_gets_all_notify_only_trusted(monkeypatch):
    captured = {}
    orders = {"o1t": _rec("o1t"), "o2g": _rec("o2g")}  # 1 trusted, 1 guessed
    _patch_run_once(monkeypatch, captured, orders)
    summary = RFS.run_once(now=_N, margin=15.0, max_orders=60)
    # jsonl: OBA rekordy (eval pełny)
    jsonl_oids = sorted(r["order_id"] for r in captured["jsonl"])
    assert jsonl_oids == ["o1t", "o2g"], f"jsonl powinien mieć oba, ma {jsonl_oids}"
    # notify: TYLKO trusted
    notif_oids = sorted(r["order_id"] for r in captured.get("notified_rows", []))
    assert notif_oids == ["o1t"], f"notify tylko trusted, ma {notif_oids}"
    # summary observability
    assert summary["tg_trusted_only"] is True
    assert summary["tg_cooldown_min"] == 20.0
    assert summary["tg_sent"] == 1
    assert summary["evaluated"] == 2 and summary["would_reassign"] == 2


def test_run_once_cooldown_dedup_across_state(monkeypatch):
    # zlecenie trusted powiadomione przed chwilą (w notified) → cooldown dławi, jsonl wciąż pełny
    captured = {}
    orders = {"o1t": _rec("o1t")}
    _patch_run_once(monkeypatch, captured, orders)
    # nadpisz _load_notified: o1t powiadomione 5 min temu
    monkeypatch.setattr(RFS, "_load_notified",
                        lambda: {"o1t": {"best": "B", "ts": (_N - timedelta(minutes=5)).isoformat()}})
    summary = RFS.run_once(now=_N, margin=15.0, max_orders=60)
    assert [r["order_id"] for r in captured["jsonl"]] == ["o1t"]   # jsonl pełny
    assert captured.get("notified_rows", "UNSET") == "UNSET" or captured.get("notified_rows") == []  # 0 notify
    assert summary["tg_sent"] == 0


# ============================ stałe modułu ============================

def test_notify_constants():
    assert RFS.NOTIFY_TRUSTED_ONLY_FLAG == "REASSIGN_FWD_NOTIFY_TRUSTED_ONLY"
    assert RFS.NOTIFY_COOLDOWN_KEY == "REASSIGN_FWD_NOTIFY_COOLDOWN_MIN"
    assert RFS.DEFAULT_NOTIFY_COOLDOWN_MIN == 20.0
    assert "gps" in RFS._REAL_POS and "pin" not in RFS._REAL_POS


if __name__ == "__main__":
    import pytest
    sys.exit(pytest.main([__file__, "-v"]))
