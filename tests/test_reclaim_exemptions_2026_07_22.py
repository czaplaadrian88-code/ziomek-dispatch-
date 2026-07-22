"""Production writer contract for czasowka reclaim exemptions."""
from __future__ import annotations

import json
from datetime import datetime, timezone

import pytest

from dispatch_v2 import reclaim_exemptions as RE
from dispatch_v2.tools import czasowka_reclaim_exempt as CLI


NOW = datetime(2026, 7, 22, 12, 0, tzinfo=timezone.utc)


def test_store_roundtrip_is_atomic_and_audited(tmp_path):
    path = tmp_path / "reclaim_exemptions.json"

    entry = RE.set_exemption("485123", "manual_time_hold", path=path, now=NOW)
    assert entry["reason_code"] == "manual_time_hold"
    assert RE.get_exemption("485123", path)["created_at"] == NOW.isoformat()
    assert path.stat().st_mode & 0o777 == 0o600

    removed = RE.remove_exemption(
        "485123", "operator_released", path=path, now=NOW
    )
    assert removed == entry
    assert RE.list_exemptions(path) == {}
    document = json.loads(path.read_text(encoding="utf-8"))
    assert [row["action"] for row in document["audit"]] == ["add", "remove"]
    assert [row["reason_code"] for row in document["audit"]] == [
        "manual_time_hold",
        "operator_released",
    ]


@pytest.mark.parametrize(
    "order_id,reason_code",
    [
        ("", "manual_time_hold"),
        ("oid-1", "manual_time_hold"),
        ("485123", "free text may contain PII"),
    ],
)
def test_store_rejects_invalid_or_free_text_input(tmp_path, order_id, reason_code):
    path = tmp_path / "reclaim_exemptions.json"
    with pytest.raises(ValueError):
        RE.set_exemption(order_id, reason_code, path=path, now=NOW)
    assert not path.exists()


def test_cli_add_list_remove_roundtrip(tmp_path, capsys):
    path = tmp_path / "reclaim_exemptions.json"
    prefix = ["--state-path", str(path)]

    assert CLI.main([*prefix, "add", "485123", "--reason-code", "investigation"]) == 0
    assert CLI.main([*prefix, "list"]) == 0
    listed = json.loads(capsys.readouterr().out.splitlines()[-1])
    assert list(listed["entries"]) == ["485123"]
    assert CLI.main(
        [*prefix, "remove", "485123", "--reason-code", "operator_released"]
    ) == 0
    assert RE.list_exemptions(path) == {}


def test_corrupt_store_is_never_overwritten(tmp_path):
    path = tmp_path / "reclaim_exemptions.json"
    path.write_text("{broken", encoding="utf-8")

    with pytest.raises(ValueError, match="malformed"):
        RE.set_exemption("485123", "investigation", path=path, now=NOW)
    assert path.read_text(encoding="utf-8") == "{broken"
