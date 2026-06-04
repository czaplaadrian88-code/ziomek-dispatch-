#!/usr/bin/env python3
"""FAIL-03-K1 KROK 1 testy helpera (log-only)."""
import sys
from datetime import datetime, timedelta, timezone
sys.path.insert(0, "/root/.openclaw/workspace/scripts")
from dispatch_v2 import common as C
from dispatch_v2 import shadow_dispatcher as SD
from dispatch_v2.dispatch_pipeline import EARLY_BIRD_THRESHOLD_MIN as T
_N = datetime(2026, 6, 4, 12, 0, 0, tzinfo=timezone.utc)
F = SD._always_propose_would_redirect_shadow


def _r(v="KOORD", r="no_solo_candidates (x)"):
    return {"verdict": v, "reason": r}


def _p(m=30, aid=999):
    return {"pickup_at_warsaw": (_N + timedelta(minutes=m)).isoformat(), "address_id": aid}


def test_all(monkeypatch):
    monkeypatch.setattr(C, "flag", lambda n, d=False: (n == "ALWAYS_PROPOSE_WOULD_REDIRECT_SHADOW") or d)
    monkeypatch.setattr(C, "FIRMOWE_KONTO_ADDRESS_IDS", frozenset({161}))
    assert F(_r(), _p(30), _N)["path"] == "no_solo_candidates"
    assert F(_r(r="best_effort_r6_breach_v2 x"), _p(30), _N)["path"] == "best_effort_r6_breach_v2"
    assert F(_r(), _p(T - 1), _N) is not None
    assert F(_r(), _p(T), _N) is None
    assert F(_r(v="PROPOSE"), _p(30), _N) is None
    assert F(_r(r="early_bird (75)"), _p(75), _N) is None
    assert F(_r(), _p(30, 161), _N) is None
    assert F(_r(), _p(30, "161"), _N) is None
    assert F(_r(), _p(30, 999), _N) is not None
    assert F(_r(), {}, _N) is None and F(_r(), None, _N) is None
    monkeypatch.setattr(C, "flag", lambda n, d=False: False)
    assert F(_r(), _p(30), _N) is None


if __name__ == "__main__":
    import pytest
    sys.exit(pytest.main([__file__, "-v"]))
