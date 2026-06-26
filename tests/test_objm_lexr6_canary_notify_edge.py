"""Edge-triggered notify dla canary objm-lexr6 — alert TYLKO przy zmianie werdyktu.

Chroni przed regresją „Telegram co 10 min" (level-triggered). Testuje pure `_notify_decision`.
"""
from datetime import datetime, timezone, timedelta

from dispatch_v2.tools import objm_lexr6_canary_monitor as M

T0 = datetime(2026, 6, 26, 12, 0, 0, tzinfo=timezone.utc)
REMIND = timedelta(hours=2)

STOP_G2B = [("G2b-auto-route", "STOP", "ACK+ALERT +8.1pp")]
WARN_G2C = [("G2c-reorder", "WARN", "dedup 36%")]


def _state(send, msg, st):
    return {"send": send, "msg": msg, "st": st}


def test_first_stop_sends():
    send, msg, st = M._notify_decision(STOP_G2B, [], {}, T0, REMIND)
    assert send is True
    assert "STOP" in msg and "G2b-auto-route" in msg
    assert st["level"] == "STOP"


def test_same_stop_within_window_silent():
    # poprzednio wysłane przed chwilą → ten sam werdykt = CISZA (nie co tick)
    prev = {"signature": M._verdict_signature(STOP_G2B, [])[1], "level": "STOP",
            "last_sent": T0.isoformat()}
    send, msg, st = M._notify_decision(STOP_G2B, [], prev, T0 + timedelta(minutes=10), REMIND)
    assert send is False
    assert msg is None
    # last_sent niezmieniony (nie przesuwamy zegara gdy nie wysyłamy)
    assert st["last_sent"] == T0.isoformat()


def test_persistent_stop_reminded_after_window():
    prev = {"signature": M._verdict_signature(STOP_G2B, [])[1], "level": "STOP",
            "last_sent": T0.isoformat()}
    send, msg, st = M._notify_decision(STOP_G2B, [], prev, T0 + timedelta(hours=2, minutes=1), REMIND)
    assert send is True
    assert "nadal" in msg
    assert st["last_sent"] == (T0 + timedelta(hours=2, minutes=1)).isoformat()


def test_escalation_new_gate_sends():
    prev = {"signature": M._verdict_signature(STOP_G2B, [])[1], "level": "STOP",
            "last_sent": T0.isoformat()}
    # ten sam STOP, ale dochodzi WARN G2c → inna sygnatura → alert mimo okna
    send, msg, st = M._notify_decision(STOP_G2B, WARN_G2C, prev, T0 + timedelta(minutes=10), REMIND)
    assert send is True


def test_recovery_to_go_sends_once():
    prev = {"signature": M._verdict_signature(STOP_G2B, [])[1], "level": "STOP",
            "last_sent": T0.isoformat()}
    send, msg, st = M._notify_decision([], [], prev, T0 + timedelta(minutes=10), REMIND)
    assert send is True
    assert "GO" in msg
    assert st["level"] == "GO"
    # kolejny GO już cisza
    send2, msg2, _ = M._notify_decision([], [], st, T0 + timedelta(minutes=20), REMIND)
    assert send2 is False


def test_steady_go_silent():
    prev = {"signature": "GO|", "level": "GO", "last_sent": None}
    send, msg, st = M._notify_decision([], [], prev, T0, REMIND)
    assert send is False


def test_warn_first_sends_warn_head():
    send, msg, st = M._notify_decision([], WARN_G2C, {}, T0, REMIND)
    assert send is True
    assert msg.startswith("🟡")
    assert st["level"] == "WARN"


def test_reminder_disabled_stays_silent():
    prev = {"signature": M._verdict_signature(STOP_G2B, [])[1], "level": "STOP",
            "last_sent": T0.isoformat()}
    send, msg, st = M._notify_decision(STOP_G2B, [], prev, T0 + timedelta(hours=99),
                                       timedelta(hours=0))
    assert send is False  # remind_after=0 → bez przypomnień, tylko zmiana wyzwala
