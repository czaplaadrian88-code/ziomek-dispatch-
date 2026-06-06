"""Read-only reader of the gastro courier roster (cid <-> name) + name->cid matcher.

Source: gastro panel ``/admin2017/list-users`` — every courier is rendered as an
``<li class="... li_hover_user_list">`` block containing:
  - ``<span class="typ_user">kurier</span>`` — role (filters out restaurant /
    admin / customer users)
  - ``onclick="activeKurier(<cid>, this)"`` toggle button where ``<cid>`` IS the
    panel ``id_kurier`` (verified live 2026-06-06 on cid 123 Bartek, 530 Bartosz,
    492 Jakub W). The button has class ``btn-success`` when the courier is ACTIVE
    in gastro, ``btn-danger`` when inactive.
  - the display name as plain text before the ``typ_user`` span.

Used by ``new_courier_pairing`` and the ``/nowy`` Telegram command to resolve the
REAL gastro ``id_kurier`` for a courier whose name shows up in the grafik but is
not yet in ``kurier_ids.json``.

Hard rules (Z2/Z3):
  - READ-ONLY. Only GET via the already-authenticated ``panel_client`` opener.
    NEVER POST. Never create a new CookieJar (would invalidate the main session,
    HTTP 419 — see dispatch_v2/CLAUDE.md "get_last_panel_position" landmine).
  - Fail-open: any error -> empty roster. Callers degrade to "ask Adrian" and
    NEVER crash dispatch.
  - Matching is conservative: ambiguous -> no auto-pick (caller asks Adrian).
"""
from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

from dispatch_v2.common import setup_logger

LOG_DIR = "/root/.openclaw/workspace/scripts/logs/"
_log = setup_logger("panel_roster", LOG_DIR + "new_courier_pairing.log")

LIST_USERS_PATH = "/admin2017/list-users"

# TTL cache — list-users is ~450KB; we read it at most a few times per scan.
_CACHE_TTL_SEC = 120.0
_cache: Dict[str, object] = {"at": 0.0, "active": None, "full": None}

# Regexes (module-level, compiled once).
_LI_SPLIT = re.compile(r"<li[^>]*li_hover_user_list[^>]*>")
_ROLE_RE = re.compile(r"typ_user\">\s*([^<]+?)\s*</span>")
_CID_RE = re.compile(r"(?:activeKurier|removeUser)\((\d+)")
_CID_FALLBACK_RE = re.compile(r"edit-user/(\d+)")
_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"\s+")


def _active_re(cid: int) -> re.Pattern:
    # activeKurier(<cid> ...)" ... class="... btn-success ..."
    return re.compile(r"activeKurier\(%d[^)]*\)\"[^>]*btn-success" % cid)


# --------------------------------------------------------------------------- #
# Parsing
# --------------------------------------------------------------------------- #


def parse_list_users(html: str) -> List[Tuple[int, str, bool]]:
    """Parse list-users HTML -> [(cid, name, is_active), ...] for kurier-role users.

    Pure function (no I/O) — directly testable from a fixture.
    """
    out: List[Tuple[int, str, bool]] = []
    seen: set = set()
    for blk in _LI_SPLIT.split(html)[1:]:
        role_m = _ROLE_RE.search(blk)
        if not role_m or role_m.group(1).strip().lower() != "kurier":
            continue
        cid_m = _CID_RE.search(blk) or _CID_FALLBACK_RE.search(blk)
        if not cid_m:
            continue
        cid = int(cid_m.group(1))
        if cid in seen:
            continue
        # Name = text before the first <span (the typ_user span follows the name).
        head = blk.split("<span", 1)[0]
        name = _WS_RE.sub(" ", _TAG_RE.sub(" ", head)).strip().strip(",").strip()
        if not name:
            continue
        is_active = bool(_active_re(cid).search(blk))
        out.append((cid, name, is_active))
        seen.add(cid)
    return out


def _fetch_list_users_html() -> Optional[str]:
    """GET /admin2017/list-users via the authenticated panel_client opener.

    Returns HTML or None (fail-open). Read-only.
    """
    try:
        from dispatch_v2 import panel_client as pc
        opener, _csrf, _ = pc.login()
        resp = opener.open(pc.BASE_URL + LIST_USERS_PATH, timeout=20)
        if "admin2017/login" in resp.url:
            _log.warning("list-users: redirected to login (session lost) — skip")
            return None
        return resp.read().decode("utf-8", "replace")
    except Exception as e:  # noqa: BLE001 — fail-open by design
        _log.warning(f"_fetch_list_users_html fail: {type(e).__name__}: {e}")
        return None


def fetch_full_roster(force: bool = False) -> List[Tuple[int, str, bool]]:
    """All kurier-role users -> [(cid, name, is_active)]. TTL-cached. Fail-open []."""
    now = time.time()
    if not force and _cache["full"] is not None and (now - float(_cache["at"])) < _CACHE_TTL_SEC:
        return _cache["full"]  # type: ignore[return-value]
    html = _fetch_list_users_html()
    if html is None:
        # keep stale cache if any, else empty
        return _cache["full"] if _cache["full"] is not None else []  # type: ignore[return-value]
    full = parse_list_users(html)
    active = {cid: name for cid, name, act in full if act}
    _cache.update({"at": now, "full": full, "active": active})
    return full


def fetch_active_roster(force: bool = False) -> Dict[int, str]:
    """Active couriers only -> {cid: name}. TTL-cached. Fail-open {}.

    "Active" == gastro toggle button is btn-success. This filters the ~300
    historical/inactive courier accounts down to the ~50 that matter.
    """
    now = time.time()
    if not force and _cache["active"] is not None and (now - float(_cache["at"])) < _CACHE_TTL_SEC:
        return _cache["active"]  # type: ignore[return-value]
    fetch_full_roster(force=force)  # populates _cache["active"] as a side-effect
    return _cache["active"] if _cache["active"] is not None else {}  # type: ignore[return-value]


# --------------------------------------------------------------------------- #
# Name -> cid matching
# --------------------------------------------------------------------------- #


def _norm_token(tok: str) -> str:
    """Lowercase + strip trailing punctuation (handles abbrev surnames 'Ch.')."""
    return (tok or "").strip().rstrip(".,;:").lower()


@dataclass
class MatchResult:
    status: str  # "matched" | "none" | "ambiguous"
    cid: Optional[int] = None
    name: Optional[str] = None  # roster display name of the winner
    score: int = 0
    candidates: List[Tuple[int, str, int]] = field(default_factory=list)  # (cid,name,score) desc


def _score(full_name: str, roster_name: str) -> int:
    """Score a grafik full name against a roster (possibly abbreviated) name.

    First name MUST match (case-insensitive). Surname matched by prefix in either
    direction ('Choiński' vs 'Ch.'). Score = length of the MATCHED (overlapping)
    prefix * 10 — this rewards the longer abbreviation so e.g. "Rafał Jankowski"
    prefers gastro "Rafał Jan" (matched 'jan'=3 → 30) over "Rafał J" (matched
    'j'=1 → 10), disambiguating two same-first-name couriers. A roster entry with
    only a first name scores 1 (weak — last resort).
    """
    sp = [t for t in (full_name or "").strip().split() if t]
    rp = [t for t in (roster_name or "").strip().split() if t]
    if not sp or not rp:
        return 0
    if _norm_token(sp[0]) != _norm_token(rp[0]):
        return 0
    s_last = _norm_token(sp[-1]) if len(sp) > 1 else ""
    r_last = _norm_token(rp[-1]) if len(rp) > 1 else ""
    if not r_last:
        return 1  # roster has bare first name only
    if not s_last:
        return 0  # grafik has only first name, roster has a surname -> mismatch
    if s_last.startswith(r_last):
        return len(r_last) * 10   # roster abbrev fully contained in grafik surname
    if r_last.startswith(s_last):
        return len(s_last) * 10
    return 0


def match_name_to_cid(
    full_name: str,
    roster: Optional[Dict[int, str]] = None,
) -> MatchResult:
    """Resolve a grafik full name to a gastro cid using the active roster.

    Conservative: a tie at the top score -> ``ambiguous`` (caller asks Adrian).
    """
    if roster is None:
        roster = fetch_active_roster()
    if not roster:
        return MatchResult(status="none")
    scored = [
        (cid, name, _score(full_name, name))
        for cid, name in roster.items()
    ]
    scored = [t for t in scored if t[2] > 0]
    scored.sort(key=lambda t: -t[2])
    if not scored:
        return MatchResult(status="none", candidates=[])
    best = scored[0]
    if len(scored) > 1 and scored[1][2] == best[2]:
        return MatchResult(status="ambiguous", candidates=scored[:5])
    return MatchResult(
        status="matched", cid=best[0], name=best[1], score=best[2],
        candidates=scored[:5],
    )
