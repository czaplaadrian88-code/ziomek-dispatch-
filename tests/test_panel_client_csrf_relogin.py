"""Regression tests for rebuilding CSRF-bearing requests after re-login."""

from __future__ import annotations

import urllib.error
import urllib.parse

import pytest

from dispatch_v2 import panel_client as pc


class _Response:
    def read(self) -> bytes:
        return b'{"zlecenie": {"id_zlecenie": "sanitized"}}'


class _FakeOpener:
    def __init__(self, failures: int = 1) -> None:
        self.failures = failures
        self.request_bodies: list[bytes] = []

    def open(self, req, timeout=None):  # noqa: ANN001, ARG002
        self.request_bodies.append(req.data)
        if len(self.request_bodies) <= self.failures:
            raise urllib.error.HTTPError("sanitized", 419, "expired", {}, None)
        return _Response()


@pytest.fixture
def isolated_panel_session(monkeypatch, tmp_path):
    """Keep the retry oracle away from network and live state."""
    previous_session = dict(pc._session)
    monkeypatch.setattr(pc, "_SESSION_CACHE_PATH", tmp_path / "session.json")
    monkeypatch.setattr(pc, "_SESSION_CACHE_LEGACY_PATH", tmp_path / "legacy.json")
    pc._session.update(
        opener=None,
        csrf=None,
        last_login_at=0.0,
        last_ok=False,
    )
    yield
    pc._session.clear()
    pc._session.update(previous_session)


def _csrf_from_body(body: bytes) -> str:
    return urllib.parse.parse_qs(body.decode())["_token"][0]


def test_success_without_419_keeps_single_request(monkeypatch, isolated_panel_session):
    opener = _FakeOpener(failures=0)
    pc._session.update(
        opener=opener,
        csrf="csrf-v1",
        last_login_at=1.0,
        last_ok=True,
    )

    def unexpected_login(force: bool = False):  # noqa: ARG001
        pytest.fail("login must not run without an authentication error")

    monkeypatch.setattr(pc, "login", unexpected_login)

    response = pc._open_with_relogin(
        lambda csrf: pc._details_request(csrf, "sanitized"),
        csrf="csrf-v1",
    )

    assert response.read()
    assert [_csrf_from_body(body) for body in opener.request_bodies] == ["csrf-v1"]


def test_419_relogin_rebuilds_request_with_fresh_csrf(
    monkeypatch, isolated_panel_session
):
    opener = _FakeOpener()
    pc._session.update(
        opener=opener,
        csrf="csrf-v1",
        last_login_at=1.0,
        last_ok=True,
    )

    def fake_login(force: bool = False):
        assert force is True
        pc._session.update(
            opener=opener,
            csrf="csrf-v2",
            last_login_at=2.0,
            last_ok=True,
        )
        return opener, "csrf-v2", None

    monkeypatch.setattr(pc, "login", fake_login)

    response = pc._open_with_relogin(
        lambda csrf: pc._details_request(csrf, "sanitized"),
        csrf="csrf-v1",
    )

    assert response.read()
    assert [_csrf_from_body(body) for body in opener.request_bodies] == [
        "csrf-v1",
        "csrf-v2",
    ]


def test_419_relogin_retries_only_once(monkeypatch, isolated_panel_session):
    opener = _FakeOpener(failures=2)
    pc._session.update(
        opener=opener,
        csrf="csrf-v1",
        last_login_at=1.0,
        last_ok=True,
    )
    login_calls = 0

    def fake_login(force: bool = False):
        nonlocal login_calls
        assert force is True
        login_calls += 1
        pc._session.update(
            opener=opener,
            csrf="csrf-v2",
            last_login_at=2.0,
            last_ok=True,
        )
        return opener, "csrf-v2", None

    monkeypatch.setattr(pc, "login", fake_login)

    with pytest.raises(urllib.error.HTTPError) as exc_info:
        pc._open_with_relogin(
            lambda csrf: pc._details_request(csrf, "sanitized"),
            csrf="csrf-v1",
        )

    assert exc_info.value.code == 419
    assert login_calls == 1
    assert [_csrf_from_body(body) for body in opener.request_bodies] == [
        "csrf-v1",
        "csrf-v2",
    ]
