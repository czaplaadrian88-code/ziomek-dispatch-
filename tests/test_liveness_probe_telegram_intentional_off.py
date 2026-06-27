"""check_telegram must NOT flag DOWN when dispatch-telegram is intentionally off.

Context (2026-06-27): dispatch-telegram is deliberately `disable --now` (C1) --
proposals are reviewed in the coordinator console, not on Telegram. The liveness
probe was flagging it `DOWN` every tick -> a Telegram meta-alert
"[ZIOMEK LIVENESS] dispatch-telegram DOWN" fired every 30-min dedup window
("Ziomek nie wysyła propozycji na telegramie"). Fix: a disabled/masked unit is an
operator decision, not a liveness failure, so the probe returns ok. A unit that is
`enabled` but inactive (real crash) must still alert (ON != OFF proof).
"""
from unittest.mock import patch

from dispatch_v2.observability import liveness_probe


def _patch_systemctl(enabled_state: str, active_state: str):
    """Patch _run so is-enabled / is-active return the given states."""
    def fake_run(cmd, timeout=5.0):
        if cmd[:2] == ["systemctl", "is-enabled"]:
            return (0, enabled_state + "\n")
        if cmd[:2] == ["systemctl", "is-active"]:
            return (0, active_state + "\n")
        return (-1, "")
    return patch.object(liveness_probe, "_run", side_effect=fake_run)


def test_disabled_telegram_is_ok_not_down():
    """Intentional `disabled` + inactive -> ok (no DOWN, no alert)."""
    with _patch_systemctl("disabled", "inactive"):
        unit, status, detail = liveness_probe.check_telegram()
    assert unit == "dispatch-telegram"
    assert status == "ok"
    assert "intentionally off" in detail
    assert "is-enabled=disabled" in detail


def test_masked_telegram_is_ok_not_down():
    with _patch_systemctl("masked", "inactive"):
        _, status, _ = liveness_probe.check_telegram()
    assert status == "ok"


def test_enabled_but_inactive_telegram_still_down():
    """ON != OFF: a real crash (enabled but inactive) must still alert DOWN."""
    with _patch_systemctl("enabled", "inactive"):
        unit, status, detail = liveness_probe.check_telegram()
    assert unit == "dispatch-telegram"
    assert status == "down"


def test_enabled_and_active_telegram_is_ok():
    with _patch_systemctl("enabled", "active"):
        _, status, detail = liveness_probe.check_telegram()
    assert status == "ok"
    assert detail == "active"


def test_static_telegram_treated_as_live_monitored():
    """`static` is not in the intentional-off set -> falls through to is-active."""
    with _patch_systemctl("static", "inactive"):
        _, status, _ = liveness_probe.check_telegram()
    assert status == "down"
