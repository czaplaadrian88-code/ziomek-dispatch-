"""Pytest config + autouse safety fixtures dla całej dispatch_v2/tests/.

Z2 fix 2026-05-07 (Lekcja #75): defense-in-depth Layer 2 against accidental
real Telegram sends z testów. Warstwy:
  L1: telegram_utils.send_admin_alert sprawdza PYTEST_CURRENT_TEST env (in-prod check)
  L2: ten conftest autouse monkeypatch (ratuje gdy L1 byłby kiedyś refaktorowany)
  L3: per-file mock_telegram fixtures (np. test_parser_health_layer3.py)

Opt-out: test wprost weryfikujący real send = `request.getfixturevalue` z markerem
LUB env ALLOW_TELEGRAM_IN_TEST=1 (omija L1).
"""
import pytest


@pytest.fixture(autouse=True)
def _block_real_telegram_sends(monkeypatch, request):
    """Default-block dla send_admin_alert na czas testu.

    Override w teście: zmocuj atrybut samodzielnie (last monkeypatch wins).
    Np. mock_telegram fixture w test_parser_health_layer3.py podmienia na
    capture-lambda i autouse override jest nadpisany.
    """
    try:
        from dispatch_v2 import telegram_utils
    except ImportError:
        return
    monkeypatch.setattr(
        telegram_utils,
        "send_admin_alert",
        lambda text: True,
        raising=False,
    )
