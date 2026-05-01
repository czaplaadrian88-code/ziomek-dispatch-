"""V3.28 R-04 v2.0 Phase 2 — semi-enforce applier z preview ACK.

Workflow (option B):
  1. preview: read tier_suggestions.json → filter applicable changes
     (skip insufficient_data, skip gold tier — manual only, skip cooldown active),
     send_admin_alert preview text, print summary.
  2. apply: same filter + atomic write courier_tiers.json (z backup), append
     tier_evolution.jsonl applied=true, send_admin_alert confirmation.

Cooldown: 7 days (per schema._meta.promotion_cooldown_days). Read tier_evolution.jsonl
for last applied=true entry per cid. Skip if cooldown still active.

Gold tier: NIGDY auto-applied (gold_candidate flag is advisory only).
sustained_days demotion gates: SUPPRESSED Phase 1+2 (no historical evolution
data yet — Phase 3+ moze replay evolution log).

Run:
  python3 -m dispatch_v2.r04_apply --preview         (default — print + Telegram)
  python3 -m dispatch_v2.r04_apply --apply            (apply ALL eligible)
  python3 -m dispatch_v2.r04_apply --apply --cids 509,393  (selective)
  python3 -m dispatch_v2.r04_apply --apply --skip-telegram (silent apply)
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from datetime import datetime, timezone, timedelta
from typing import Any, Dict, List, Optional, Set

from dispatch_v2 import common as C
from dispatch_v2 import telegram_utils

SUGGESTIONS_PATH = "/root/.openclaw/workspace/dispatch_state/tier_suggestions.json"
COURIER_TIERS_PATH = "/root/.openclaw/workspace/dispatch_state/courier_tiers.json"
EVOLUTION_LOG = "/root/.openclaw/workspace/dispatch_state/tier_evolution.jsonl"
SCHEMA_PATH = "/root/.openclaw/workspace/dispatch_state/r04_schema.json"

# Map schema canonical names → courier_tiers.json short form
_TIER_SHORT = {
    "standard_plus": "std+",
    "standard": "std",
    "gold": "gold",
    "slow": "slow",
    "new": "new",
}

log = logging.getLogger(__name__)


def _load_json(path: str) -> Dict[str, Any]:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def _last_applied_per_cid(evolution_path: str) -> Dict[str, str]:
    """Return {cid: latest_applied_ts} from tier_evolution.jsonl entries gdzie applied=true."""
    out: Dict[str, str] = {}
    try:
        with open(evolution_path, "r", encoding="utf-8") as f:
            for line in f:
                try:
                    e = json.loads(line)
                except Exception:
                    continue
                if not e.get("applied"):
                    continue
                cid = str(e.get("cid") or "")
                if not cid:
                    continue
                ts = e.get("ts") or ""
                if ts and (cid not in out or ts > out[cid]):
                    out[cid] = ts
    except FileNotFoundError:
        pass
    except Exception as e:
        log.error(f"_last_applied_per_cid fail: {e}")
    return out


def _cooldown_active(cid: str, last_applied: Dict[str, str], cooldown_days: int, now_utc: datetime) -> Optional[str]:
    """Return human reason if cooldown active (cid changed within cooldown_days), else None."""
    last = last_applied.get(str(cid))
    if not last:
        return None
    try:
        last_dt = datetime.fromisoformat(last.replace("Z", "+00:00"))
        if last_dt.tzinfo is None:
            last_dt = last_dt.replace(tzinfo=timezone.utc)
    except Exception:
        return None
    delta = now_utc - last_dt
    if delta < timedelta(days=cooldown_days):
        days_left = cooldown_days - delta.days
        return f"cooldown active ({delta.days}d/{cooldown_days}d, {days_left}d remaining)"
    return None


def _build_eligible_changes(
    suggestions: Dict[str, Any],
    schema: Dict[str, Any],
    last_applied: Dict[str, str],
    now_utc: datetime,
    cid_filter: Optional[Set[str]] = None,
) -> Dict[str, List[Dict[str, Any]]]:
    """Filter suggestions → buckets: eligible, skip_gold, skip_insufficient,
    skip_match, skip_cooldown, skip_unsupported_target.
    """
    cooldown_days = int(schema.get("_meta", {}).get("promotion_cooldown_days", 7))
    buckets: Dict[str, List[Dict[str, Any]]] = {
        "eligible": [],
        "skip_match": [],
        "skip_insufficient": [],
        "skip_gold_current": [],
        "skip_gold_target": [],
        "skip_cooldown": [],
        "skip_unsupported_target": [],
    }
    for cid, s in suggestions.items():
        if cid == "_meta":
            continue
        if cid_filter is not None and cid not in cid_filter:
            continue
        cur = s.get("current_tier")
        sug = s.get("suggested_tier")
        item = {
            "cid": cid,
            "name": s.get("name"),
            "current_tier": cur,
            "suggested_tier": sug,
            "current_tier_short": _TIER_SHORT.get(cur, cur),
            "suggested_tier_short": _TIER_SHORT.get(sug, sug),
            "gold_candidate": s.get("gold_candidate"),
            "reasoning": s.get("reasoning"),
        }
        if s.get("tier_match"):
            buckets["skip_match"].append(item)
            continue
        if s.get("insufficient_data"):
            buckets["skip_insufficient"].append(item)
            continue
        # Gold guard: never auto-promote/demote z/do gold (manual only)
        if cur == "gold":
            buckets["skip_gold_current"].append(item)
            continue
        if sug == "gold":
            buckets["skip_gold_target"].append(item)
            continue
        if sug not in _TIER_SHORT:
            buckets["skip_unsupported_target"].append(item)
            continue
        cd_reason = _cooldown_active(cid, last_applied, cooldown_days, now_utc)
        if cd_reason:
            item["cooldown_reason"] = cd_reason
            buckets["skip_cooldown"].append(item)
            continue
        buckets["eligible"].append(item)
    return buckets


def _format_preview(buckets: Dict[str, List[Dict[str, Any]]], dry_run: bool) -> str:
    lines: List[str] = []
    eligible = buckets["eligible"]
    cooldown = buckets["skip_cooldown"]
    insufficient = buckets["skip_insufficient"]
    title = "R-04 v2.0 PREVIEW" if dry_run else "R-04 v2.0 APPLY"
    lines.append(f"🎓 {title} ({datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')})")
    lines.append(f"Eligible changes: {len(eligible)}")
    if eligible:
        for it in eligible:
            arrow = "→"
            lines.append(
                f"  {it['cid']:>4} {it['name']}: {it['current_tier_short']} {arrow} "
                f"{it['suggested_tier_short']}"
            )
    if cooldown:
        lines.append(f"Skipped (cooldown): {len(cooldown)}")
        for it in cooldown[:5]:
            lines.append(f"  {it['cid']} {it['name']}: {it.get('cooldown_reason')}")
    if insufficient:
        lines.append(f"Skipped (insufficient data): {len(insufficient)}")
    skip_gold = len(buckets["skip_gold_current"]) + len(buckets["skip_gold_target"])
    if skip_gold:
        lines.append(f"Skipped (gold manual-only): {skip_gold}")
    if dry_run:
        lines.append("")
        lines.append("Aby zastosować: python3 -m dispatch_v2.r04_apply --apply")
    return "\n".join(lines)


def _atomic_write_courier_tiers(tiers: Dict[str, Any], path: str) -> None:
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(tiers, f, indent=2, ensure_ascii=False)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)


def _apply_changes(
    eligible: List[Dict[str, Any]],
    tiers_path: str,
    evolution_path: str,
    now_utc: datetime,
) -> Dict[str, Any]:
    """Atomic apply: backup → load → mutate → write → append evolution."""
    backup = f"{tiers_path}.bak-pre-r04-apply-{now_utc.strftime('%Y%m%d-%H%M%S')}"
    # Read current tiers
    tiers = _load_json(tiers_path)
    # Backup
    with open(backup, "w", encoding="utf-8") as f:
        json.dump(tiers, f, indent=2, ensure_ascii=False)
    # Mutate
    applied: List[Dict[str, Any]] = []
    for it in eligible:
        cid = it["cid"]
        old_tier_short = it["current_tier_short"]
        new_tier_short = it["suggested_tier_short"]
        entry = tiers.get(cid)
        if not isinstance(entry, dict):
            log.warning(f"apply skip cid={cid}: no entry in courier_tiers.json")
            continue
        bag = entry.get("bag")
        if not isinstance(bag, dict):
            log.warning(f"apply skip cid={cid}: bag dict missing")
            continue
        prev_short = bag.get("tier")
        bag["tier"] = new_tier_short
        applied.append({
            "cid": cid,
            "name": it.get("name"),
            "prev_tier": prev_short,
            "new_tier": new_tier_short,
        })
    # Update _meta last_manual_edit (using "auto-r04" marker)
    if "_meta" in tiers and isinstance(tiers["_meta"], dict):
        tiers["_meta"]["last_r04_apply"] = now_utc.isoformat()
        tiers["_meta"]["r04_schema_version"] = "2.0"
    # Atomic write
    _atomic_write_courier_tiers(tiers, tiers_path)
    # Append evolution log entries
    try:
        with open(evolution_path, "a", encoding="utf-8") as f:
            for a in applied:
                rec = {
                    "ts": now_utc.isoformat(),
                    "cid": a["cid"],
                    "name": a["name"],
                    "prev_tier": a["prev_tier"],
                    "new_tier": a["new_tier"],
                    "applied": True,
                    "phase": "phase2_enforce",
                    "schema_version": "2.0",
                }
                f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    except Exception as e:
        log.error(f"append evolution post-apply fail: {e}")
    return {
        "applied_count": len(applied),
        "backup_path": backup,
        "applied": applied,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="R-04 v2.0 Phase 2 applier")
    parser.add_argument("--preview", action="store_true", help="preview only (default)")
    parser.add_argument("--apply", action="store_true", help="apply changes to courier_tiers.json")
    parser.add_argument("--cids", help="comma-separated cid filter (apply selective)")
    parser.add_argument("--skip-telegram", action="store_true", help="don't send Telegram alert")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] r04_apply: %(message)s",
    )

    if not getattr(C, "ENABLE_R04_ENFORCE", False) and args.apply:
        log.error("ENABLE_R04_ENFORCE=0 — apply mode disabled. Set env to enable.")
        return 2

    cid_filter: Optional[Set[str]] = None
    if args.cids:
        cid_filter = {x.strip() for x in args.cids.split(",") if x.strip()}

    now_utc = datetime.now(timezone.utc)
    schema = _load_json(SCHEMA_PATH)
    suggestions_full = _load_json(SUGGESTIONS_PATH)
    suggestions = {k: v for k, v in suggestions_full.items() if k != "_meta"}
    last_applied = _last_applied_per_cid(EVOLUTION_LOG)

    buckets = _build_eligible_changes(suggestions, schema, last_applied, now_utc, cid_filter)
    eligible = buckets["eligible"]

    dry_run = args.preview or not args.apply
    preview_text = _format_preview(buckets, dry_run=dry_run)
    print(preview_text)

    if args.apply and eligible:
        result = _apply_changes(eligible, COURIER_TIERS_PATH, EVOLUTION_LOG, now_utc)
        print()
        print(f"✅ Applied {result['applied_count']} tier changes")
        print(f"Backup: {result['backup_path']}")
        if not args.skip_telegram:
            try:
                msg = "✅ R-04 APPLIED:\n" + "\n".join(
                    f"  {a['cid']} {a['name']}: {a['prev_tier']} → {a['new_tier']}"
                    for a in result["applied"]
                )
                telegram_utils.send_admin_alert(msg)
            except Exception as e:
                log.error(f"telegram apply alert fail: {e}")
        return 0

    if dry_run and not args.skip_telegram and (eligible or buckets["skip_cooldown"]):
        try:
            telegram_utils.send_admin_alert(preview_text)
        except Exception as e:
            log.error(f"telegram preview alert fail: {e}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
