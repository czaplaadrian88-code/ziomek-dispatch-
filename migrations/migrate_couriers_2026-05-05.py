"""ONE-SHOT migracja kurierów — Sprawa #1 (2026-05-05).

Cel: zsynchronizować WSZYSTKICH kurierów z aktualnego grafiku
(`schedule_today.json`) z trzema storami Ziomka:
- `kurier_ids.json`     (panel_name -> cid)
- `courier_tiers.json`  (cid -> tier nested)
- `kurier_piny.json`    (PIN -> panel_name)

Skrypt jest idempotentny w odniesieniu do już-zmigrowanych kurierów
(audit raportuje `mapped` osobno). Atomic per-record z rollbackiem.

NIE pełen TASK D (czwartek 07.05) — TASK D buduje stałą feature dla
future onboarding. Dziś: wszyscy z dzisiejszego grafiku.

Subcommands:
  --audit
      Krok 1. Cross-reference grafiku vs 3 storów. Output:
      console + Telegram DM Adrianowi (admin_id z config).

  --apply <input_file>
      Krok 4. Parse Adrian's response z text-pliku, atomic add do
      3 storów per kurier. Auto-PIN gen.

  --verify
      Krok 6. Verify że nowi kurierzy są w 3 storach.

Hard rules:
- atomic per-kurier (all-or-nothing 3 stores via rollback)
- fcntl.LOCK_EX + temp + fsync + rename per Lekcja #71
- NIE wywołuje schedule writer / dispatch engine — pliki R/W tylko 3
- NIE wykonuje I/O w prod gdy `--dry-run`

Run:
  /root/.openclaw/venvs/dispatch/bin/python -m dispatch_v2.migrations.migrate_couriers_2026-05-05 --audit
  /root/.openclaw/venvs/dispatch/bin/python -m dispatch_v2.migrations.migrate_couriers_2026-05-05 --apply /tmp/adrian_response.txt
  /root/.openclaw/venvs/dispatch/bin/python -m dispatch_v2.migrations.migrate_couriers_2026-05-05 --verify
"""
from __future__ import annotations

import argparse
import fcntl
import json
import logging
import os
import random
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

log = logging.getLogger("migrate_couriers_2026_05_05")

# Paths (production)
DISPATCH_STATE = "/root/.openclaw/workspace/dispatch_state"
SCHEDULE_PATH = f"{DISPATCH_STATE}/schedule_today.json"
KURIER_IDS_PATH = f"{DISPATCH_STATE}/kurier_ids.json"
KURIER_PINY_PATH = f"{DISPATCH_STATE}/kurier_piny.json"
COURIER_TIERS_PATH = f"{DISPATCH_STATE}/courier_tiers.json"
LEARNING_LOG_PATH = f"{DISPATCH_STATE}/learning_log.jsonl"

# Tier vocab — input from Adrian (human) -> internal (code)
HUMAN_TO_INTERNAL_TIER = {
    "gold": "gold",
    "std+": "std+",
    "standard+": "std+",
    "standard plus": "std+",
    "std": "std",
    "standard": "std",
    "slow": "slow",
    "new": "new",
}
VALID_INTERNAL_TIERS = {"gold", "std+", "std", "slow", "new"}

# PIN excluded patterns (auto-generator MUST NOT emit these)
EXCLUDED_PINS = {
    "0000", "1111", "2222", "3333", "4444", "5555", "6666", "7777", "8888", "9999",
    "1234", "2345", "3456", "4567", "5678", "6789",
    "9876", "8765", "7654", "6543", "5432", "4321", "3210",
    "0123",
}


# ---------------------------------------------------------------------------
# Small file helpers (atomic write, lock, log)
# ---------------------------------------------------------------------------

def _load_json(path: str) -> Any:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def _atomic_write_json(path: str, data: Any) -> None:
    """Atomic write: fcntl.LOCK_EX on companion .lock + tempfile + fsync + rename.

    Per Lekcja #71. The lock companion file is `<path>.lock`.
    """
    lock_path = path + ".lock"
    # Ensure lock companion exists (create empty if needed)
    if not os.path.exists(lock_path):
        Path(lock_path).touch()
    with open(lock_path, "r+") as lockfh:
        fcntl.flock(lockfh.fileno(), fcntl.LOCK_EX)
        try:
            dir_ = os.path.dirname(path) or "."
            fd, tmp = tempfile.mkstemp(prefix=".migrate_", dir=dir_)
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as f:
                    json.dump(data, f, indent=2, ensure_ascii=False)
                    f.flush()
                    os.fsync(f.fileno())
                os.replace(tmp, path)
            except Exception:
                if os.path.exists(tmp):
                    try:
                        os.remove(tmp)
                    except OSError:
                        pass
                raise
        finally:
            fcntl.flock(lockfh.fileno(), fcntl.LOCK_UN)


def _append_learning_log(event: Dict[str, Any], path: str = LEARNING_LOG_PATH) -> None:
    """Append a single JSON line to learning_log.jsonl. Best-effort, NIE krytyczny."""
    try:
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(event, ensure_ascii=False) + "\n")
    except OSError as e:
        log.warning(f"learning_log append failed: {e}")


# ---------------------------------------------------------------------------
# PIN generator
# ---------------------------------------------------------------------------

def _is_excluded_pin(pin: str) -> bool:
    """True jeśli PIN matches obvious patterns (4 same digit / ascending /
    descending / repeating pair / w EXCLUDED_PINS)."""
    if not (isinstance(pin, str) and len(pin) == 4 and pin.isdigit()):
        return True
    if pin in EXCLUDED_PINS:
        return True
    # 4 same digits
    if pin[0] == pin[1] == pin[2] == pin[3]:
        return True
    # repeating pair (e.g. 1212, 2525)
    if pin[0] == pin[2] and pin[1] == pin[3]:
        return True
    # ascending sequence step=1
    if all(int(pin[i + 1]) - int(pin[i]) == 1 for i in range(3)):
        return True
    # descending sequence step=1
    if all(int(pin[i]) - int(pin[i + 1]) == 1 for i in range(3)):
        return True
    return False


def generate_pin(used_pins: set, max_attempts: int = 10000) -> str:
    """4-digit, random, collision-checked vs used_pins. Excluded:
    obvious patterns (0000, 1111, 1234, 4321, repeating digits,
    ascending/descending sequences)."""
    rng = random.SystemRandom()
    for _ in range(max_attempts):
        pin = f"{rng.randint(0, 9999):04d}"
        if pin in used_pins:
            continue
        if _is_excluded_pin(pin):
            continue
        return pin
    raise RuntimeError(
        f"Cannot generate fresh PIN after {max_attempts} attempts — "
        f"used_pins size={len(used_pins)}"
    )


# ---------------------------------------------------------------------------
# Schedule filter (skip noise rows like "Opony, odpisac na maila carefleetu")
# ---------------------------------------------------------------------------

def _is_likely_courier_name(name: str) -> bool:
    """Heuristic: 2 (lub 3) tokens, każdy zaczyna od dużej litery, brak
    interpunkcji w środku (poza myślnikiem). Wykluczamy noise typu
    "Opony, odpisac na maila carefleetu" lub "Aku pada"."""
    if not isinstance(name, str):
        return False
    n = name.strip()
    if not n:
        return False
    # Reject if comma anywhere (noise lines are often comma-separated descriptions)
    if "," in n:
        return False
    parts = n.split()
    # Real courier name is 1-3 tokens. Mostly 2 (Imię Nazwisko).
    if not (1 <= len(parts) <= 3):
        return False
    # Each token must start with uppercase (Polish letters allowed)
    for p in parts:
        if not p:
            return False
        if not p[0].isupper():
            return False
    # Reject single-token "noise" that happens to be capitalized — e.g. "Adrian"
    # alone (which IS a real entry — cid=21!). Allow single token only if no
    # punctuation and length <= 12. Otherwise require >=2 tokens.
    if len(parts) == 1 and len(parts[0]) > 12:
        return False
    return True


def _normalize_panel_name_match(full_name: str, kurier_ids: Dict[str, int]) -> Optional[str]:
    """Try to match full_name (e.g. "Bartek Ołdziej") against existing
    panel_names in kurier_ids (e.g. "Bartek O"). Returns matching panel_name
    or None.

    Strategy: exact match first; then "FirstName + first letter of lastname"
    pattern; then prefix match on first name only.
    """
    if full_name in kurier_ids:
        return full_name
    parts = full_name.split()
    if not parts:
        return None
    first = parts[0]
    # "Bartek Ołdziej" -> "Bartek O"
    if len(parts) >= 2:
        candidate = f"{first} {parts[1][0]}"
        if candidate in kurier_ids:
            return candidate
        # Two-letter abbreviation: "Adrian Cit" for "Adrian Citko"
        candidate2 = f"{first} {parts[1][:2]}"
        if candidate2 in kurier_ids:
            return candidate2
        # Three-letter abbreviation: "Mateusz Bro" for "Mateusz Brodowski"
        candidate3 = f"{first} {parts[1][:3]}"
        if candidate3 in kurier_ids:
            return candidate3
    # Single-token first-name match (only if unique among kurier_ids keys)
    matches = [k for k in kurier_ids if k.split()[0] == first and len(k.split()) <= 2]
    if len(matches) == 1:
        return matches[0]
    return None


# ---------------------------------------------------------------------------
# AUDIT (Krok 1) — cross-reference schedule vs 3 stores
# ---------------------------------------------------------------------------

def _build_audit(
    schedule: Dict[str, Any],
    kurier_ids: Dict[str, int],
    courier_tiers: Dict[str, Any],
    kurier_piny: Dict[str, str],
) -> Dict[str, List[Dict[str, Any]]]:
    """Cross-reference sched -> 3 stores. Returns:
        {
          "mapped": [...],     # all 3 stores have entry
          "partial": [...],    # cid known, tier present, brak PIN
          "unmapped": [...],   # nowi kurierzy w grafiku, brak cid
          "skipped_noise": [...],
        }
    Each entry: {"full_name", "panel_name", "cid", "tier", "shift", "missing"}
    """
    mapped: List[Dict[str, Any]] = []
    partial: List[Dict[str, Any]] = []
    unmapped: List[Dict[str, Any]] = []
    skipped_noise: List[Dict[str, Any]] = []

    couriers = schedule.get("couriers") or {}
    # Build PIN-by-panel-name map (kurier_piny is PIN-keyed)
    pins_by_panel: Dict[str, str] = {}
    for pin_str, panel_name in kurier_piny.items():
        if isinstance(panel_name, str):
            pins_by_panel.setdefault(panel_name, pin_str)

    for full_name, shift in couriers.items():
        if not _is_likely_courier_name(full_name):
            skipped_noise.append({"full_name": full_name})
            continue
        shift_str = "—"
        if isinstance(shift, dict):
            s, e = shift.get("start"), shift.get("end")
            if s and e:
                shift_str = f"{s}-{e}"
        else:
            shift_str = "off"

        panel_name = _normalize_panel_name_match(full_name, kurier_ids)
        if not panel_name:
            unmapped.append({
                "full_name": full_name,
                "panel_name": None,
                "cid": None,
                "tier": None,
                "shift": shift_str,
                "missing": ["kurier_ids", "courier_tiers", "kurier_piny"],
            })
            continue

        cid = kurier_ids[panel_name]
        cid_s = str(cid)
        tier = None
        tier_entry = courier_tiers.get(cid_s) if isinstance(courier_tiers, dict) else None
        if isinstance(tier_entry, dict):
            bag = tier_entry.get("bag") or {}
            if isinstance(bag, dict):
                tier = bag.get("tier")
        has_pin = panel_name in pins_by_panel

        missing = []
        if tier is None:
            missing.append("courier_tiers")
        if not has_pin:
            missing.append("kurier_piny")

        record = {
            "full_name": full_name,
            "panel_name": panel_name,
            "cid": cid,
            "tier": tier,
            "shift": shift_str,
            "missing": missing,
        }
        if not missing:
            mapped.append(record)
        elif missing == ["kurier_piny"]:
            partial.append(record)
        else:
            # cid known but tier missing OR multiple missing — treat as partial
            # if tier missing but PIN present, or as needing tier+PIN
            partial.append(record)

    return {
        "mapped": mapped,
        "partial": partial,
        "unmapped": unmapped,
        "skipped_noise": skipped_noise,
    }


def _format_audit_telegram(buckets: Dict[str, List[Dict[str, Any]]]) -> str:
    """Format the Telegram message body per Adrian's spec."""
    mapped = buckets["mapped"]
    partial = buckets["partial"]
    unmapped = buckets["unmapped"]
    total_sched = len(mapped) + len(partial) + len(unmapped)

    lines: List[str] = []
    lines.append("📋 Migracja kurierów — Sprawa #1")
    lines.append("")
    lines.append(f"Audit grafiku {total_sched} kurierów:")
    lines.append(f"- Mapped fully (kurier_ids + tiers + PIN): {len(mapped)}")
    lines.append(f"- Mapped partial (brak PIN/tier): {len(partial)}")
    lines.append(f"- UNMAPPED (nowi w grafiku): {len(unmapped)}")
    lines.append("")
    if unmapped or partial:
        lines.append(f"Brakujące mappingi ({len(unmapped) + len(partial)} total):")
        lines.append("")
    if unmapped:
        lines.append("UNMAPPED:")
        for i, r in enumerate(unmapped, start=1):
            lines.append(f"{i}. {r['full_name']} ({r['shift']})")
        lines.append("")
    if partial:
        lines.append("PARTIAL:")
        for i, r in enumerate(partial, start=1):
            miss = "+".join(r["missing"])
            tier_part = f" tier={r['tier']}" if r["tier"] else " brak tier"
            lines.append(
                f"{i}. {r['full_name']} (cid={r['cid']} known,{tier_part}; brakuje: {miss})"
            )
        lines.append("")
    lines.append("Dla UNMAPPED podaj: cid + tier")
    lines.append("Dla PARTIAL podaj: tylko potwierdzenie tier (PIN auto-gen)")
    lines.append("")
    lines.append("Format jednoznaczny — jeden mapping per linia:")
    lines.append("Dawid Charytoniuk 524 Standard")
    lines.append("[Imię 2] [cid] [tier]")
    lines.append("")
    lines.append("Tiery: gold | Std+ | Standard | Slow")
    lines.append("")
    lines.append("Auto-PIN zostanie wygenerowany dla każdego.")
    lines.append("Po Twojej odpowiedzi — atomic add do kurier_ids + tiers + piny.")
    return "\n".join(lines)


def cmd_audit(send_telegram: bool = True) -> int:
    """Krok 1: dump audit to console + (opcjonalnie) Telegram DM Adriana."""
    schedule = _load_json(SCHEDULE_PATH)
    kurier_ids = _load_json(KURIER_IDS_PATH)
    courier_tiers = _load_json(COURIER_TIERS_PATH)
    kurier_piny = _load_json(KURIER_PINY_PATH)

    buckets = _build_audit(schedule, kurier_ids, courier_tiers, kurier_piny)
    text = _format_audit_telegram(buckets)

    print(text)
    print()
    print(f"--- DEBUG --- mapped={len(buckets['mapped'])} "
          f"partial={len(buckets['partial'])} "
          f"unmapped={len(buckets['unmapped'])} "
          f"skipped_noise={len(buckets['skipped_noise'])}")
    if buckets["skipped_noise"]:
        print("Skipped noise rows:")
        for r in buckets["skipped_noise"]:
            print(f"  - {r['full_name'][:60]}")

    if send_telegram:
        try:
            from dispatch_v2.telegram_utils import send_admin_alert
            ok = send_admin_alert(text)
            print(f"\nTelegram send: {'OK' if ok else 'FAIL (check log)'}")
        except Exception as e:
            print(f"\nTelegram send: SKIPPED ({type(e).__name__}: {e})")
    return 0


# ---------------------------------------------------------------------------
# PARSE response (Krok 4 input)
# ---------------------------------------------------------------------------

def parse_response(
    text: str,
    audit_buckets: Dict[str, List[Dict[str, Any]]],
    kurier_ids: Dict[str, int],
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """Parse Adrian's response into list of valid migration tuples + skip list.

    Each line:  <full_name> <cid> <tier>
    Empty / `#`-prefix lines: silent skip.
    """
    valid: List[Dict[str, Any]] = []
    skipped: List[Dict[str, Any]] = []

    # Build lookups
    sched_names = {r["full_name"] for r in audit_buckets["unmapped"]}
    sched_names |= {r["full_name"] for r in audit_buckets["partial"]}
    sched_names |= {r["full_name"] for r in audit_buckets["mapped"]}
    partial_by_name = {r["full_name"]: r for r in audit_buckets["partial"]}
    existing_cids = {int(v) for v in kurier_ids.values()}

    # Track cids seen in input (duplicate detect within same response)
    seen_cids: set = set()

    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        # Last 2 tokens are cid + tier; rest is full_name (Polish names with spaces)
        tokens = line.split()
        if len(tokens) < 3:
            skipped.append({"line": raw_line, "reason": "too few tokens (need name cid tier)"})
            continue
        tier_raw = tokens[-1]
        cid_raw = tokens[-2]
        full_name = " ".join(tokens[:-2])

        # Normalize tier
        tier = HUMAN_TO_INTERNAL_TIER.get(tier_raw.lower())
        if tier is None or tier not in VALID_INTERNAL_TIERS:
            skipped.append({
                "line": raw_line,
                "reason": f"unknown tier '{tier_raw}' (valid: gold|Std+|Standard|Slow)",
            })
            continue

        # PARTIAL detection — already-known panel_name → only PIN needed
        if full_name in partial_by_name:
            rec = partial_by_name[full_name]
            valid.append({
                "full_name": full_name,
                "panel_name": rec["panel_name"],
                "cid": rec["cid"],
                "tier": tier,
                "kind": "partial",
                "missing": rec["missing"],
                "line": raw_line,
            })
            continue

        # UNMAPPED — need full add
        try:
            cid_int = int(cid_raw)
        except ValueError:
            skipped.append({
                "line": raw_line,
                "reason": f"cid '{cid_raw}' is not an integer",
            })
            continue

        if cid_int in existing_cids:
            skipped.append({
                "line": raw_line,
                "reason": f"cid={cid_int} already in kurier_ids (duplicate)",
            })
            continue
        if cid_int in seen_cids:
            skipped.append({
                "line": raw_line,
                "reason": f"cid={cid_int} duplicated within input",
            })
            continue
        seen_cids.add(cid_int)

        if full_name not in sched_names:
            skipped.append({
                "line": raw_line,
                "reason": f"name '{full_name}' not in schedule_today.json (typo?)",
            })
            continue

        # Synthesize panel_name as "First L" (first letter of last name)
        parts = full_name.split()
        if len(parts) >= 2:
            panel_name_proposed = f"{parts[0]} {parts[1][0]}"
        else:
            panel_name_proposed = full_name
        # Avoid panel_name collision
        if panel_name_proposed in kurier_ids:
            # fall back to first three letters of last name
            if len(parts) >= 2:
                panel_name_proposed = f"{parts[0]} {parts[1][:3]}"
            if panel_name_proposed in kurier_ids:
                skipped.append({
                    "line": raw_line,
                    "reason": f"panel_name collision '{panel_name_proposed}'",
                })
                continue

        valid.append({
            "full_name": full_name,
            "panel_name": panel_name_proposed,
            "cid": cid_int,
            "tier": tier,
            "kind": "unmapped",
            "missing": ["kurier_ids", "courier_tiers", "kurier_piny"],
            "line": raw_line,
        })

    return valid, skipped


# ---------------------------------------------------------------------------
# Atomic per-record migration (3 stores, all-or-nothing)
# ---------------------------------------------------------------------------

def migrate_one(
    panel_name: str,
    cid: int,
    tier: str,
    full_name: str,
    missing: List[str],
    *,
    kurier_ids_path: str = KURIER_IDS_PATH,
    courier_tiers_path: str = COURIER_TIERS_PATH,
    kurier_piny_path: str = KURIER_PINY_PATH,
    learning_log_path: str = LEARNING_LOG_PATH,
) -> Tuple[bool, str, Optional[str]]:
    """Atomic add do 3 stores. Rollback gdy step 2 lub 3 fail.

    `missing` enum subset: {"kurier_ids", "courier_tiers", "kurier_piny"}.
    Steps written ONLY for missing entries (idempotent dla mapped fields).
    Returns (success, message, pin_assigned_or_None).
    """
    cid_s = str(cid)
    pin_assigned: Optional[str] = None
    rollback_needed: List[str] = []

    # Snapshot loads (used dla PIN collision check + rollback restoration)
    kurier_ids = _load_json(kurier_ids_path)
    courier_tiers = _load_json(courier_tiers_path)
    kurier_piny = _load_json(kurier_piny_path)

    snap_ids = dict(kurier_ids)
    snap_tiers = json.loads(json.dumps(courier_tiers))  # deep copy
    snap_piny = dict(kurier_piny)

    try:
        # Step 1: kurier_ids
        if "kurier_ids" in missing:
            if panel_name in kurier_ids:
                return (False, f"panel_name={panel_name} already in kurier_ids", None)
            if cid in {int(v) for v in kurier_ids.values()}:
                return (False, f"cid={cid} already in kurier_ids (duplicate)", None)
            kurier_ids[panel_name] = cid
            _atomic_write_json(kurier_ids_path, kurier_ids)
            rollback_needed.append("kurier_ids")

        # Step 2: courier_tiers
        if "courier_tiers" in missing:
            if cid_s in courier_tiers and isinstance(courier_tiers[cid_s], dict):
                # Idempotent: just ensure bag.tier set
                entry = courier_tiers[cid_s]
                if "bag" not in entry or not isinstance(entry["bag"], dict):
                    entry["bag"] = {"tier": tier, "cap_override": None}
                else:
                    entry["bag"]["tier"] = tier
            else:
                courier_tiers[cid_s] = {
                    "name": full_name,
                    "bag": {"tier": tier, "cap_override": None},
                    "_meta": {
                        "added_via": "migrate_couriers_2026-05-05",
                        "added_at": datetime.now(timezone.utc).isoformat(),
                    },
                }
            _atomic_write_json(courier_tiers_path, courier_tiers)
            rollback_needed.append("courier_tiers")

        # Step 3: kurier_piny — generate fresh PIN
        if "kurier_piny" in missing:
            used = set(kurier_piny.keys())
            pin_assigned = generate_pin(used)
            kurier_piny[pin_assigned] = panel_name
            _atomic_write_json(kurier_piny_path, kurier_piny)
            rollback_needed.append("kurier_piny")

        # Learning log (best-effort — no rollback on failure)
        _append_learning_log({
            "ts": datetime.now(timezone.utc).isoformat(),
            "event": "MIGRATE_COURIER_2026_05_05",
            "full_name": full_name,
            "panel_name": panel_name,
            "cid": cid,
            "tier": tier,
            "pin_assigned": pin_assigned,
            "missing_filled": missing,
        }, path=learning_log_path)

        return (True, f"OK panel={panel_name} cid={cid} tier={tier}", pin_assigned)

    except Exception as e:
        # Rollback in reverse order
        log.error(f"migrate_one failed for {full_name} ({type(e).__name__}: {e}); "
                  f"rolling back {rollback_needed}")
        try:
            if "kurier_piny" in rollback_needed:
                _atomic_write_json(kurier_piny_path, snap_piny)
            if "courier_tiers" in rollback_needed:
                _atomic_write_json(courier_tiers_path, snap_tiers)
            if "kurier_ids" in rollback_needed:
                _atomic_write_json(kurier_ids_path, snap_ids)
        except Exception as rollback_err:
            log.critical(
                f"ROLLBACK FAILED for {full_name}: {rollback_err}. "
                f"Manual cleanup required."
            )
            return (False, f"FAIL {type(e).__name__}: {e} + ROLLBACK FAILED: "
                          f"{rollback_err}", None)
        return (False, f"FAIL {type(e).__name__}: {e} (rolled back)", None)


# ---------------------------------------------------------------------------
# APPLY (Krok 4)
# ---------------------------------------------------------------------------

def cmd_apply(input_file: str, send_telegram: bool = True, dry_run: bool = False) -> int:
    """Krok 4: parse Adrian's response + atomic migrate per kurier."""
    if not os.path.exists(input_file):
        print(f"ERROR: input file not found: {input_file}")
        return 2

    with open(input_file, "r", encoding="utf-8") as f:
        response_text = f.read()

    schedule = _load_json(SCHEDULE_PATH)
    kurier_ids = _load_json(KURIER_IDS_PATH)
    courier_tiers = _load_json(COURIER_TIERS_PATH)
    kurier_piny = _load_json(KURIER_PINY_PATH)

    audit_buckets = _build_audit(schedule, kurier_ids, courier_tiers, kurier_piny)
    valid, skipped = parse_response(response_text, audit_buckets, kurier_ids)

    print(f"Parsed: {len(valid)} valid, {len(skipped)} skipped")
    if skipped:
        print("Skipped lines:")
        for s in skipped:
            print(f"  - {s['line']!r}: {s['reason']}")

    if dry_run:
        print("\n--- DRY RUN ---")
        for v in valid:
            print(f"  WOULD APPLY: {v['full_name']} -> "
                  f"panel={v['panel_name']} cid={v['cid']} tier={v['tier']} "
                  f"missing={v['missing']}")
        return 0

    successes: List[Dict[str, Any]] = []
    failures: List[Dict[str, Any]] = []

    for v in valid:
        ok, msg, pin = migrate_one(
            panel_name=v["panel_name"],
            cid=v["cid"],
            tier=v["tier"],
            full_name=v["full_name"],
            missing=v["missing"],
        )
        record = {**v, "msg": msg, "pin": pin}
        if ok:
            successes.append(record)
            print(f"  OK   {v['full_name']} -> cid={v['cid']} tier={v['tier']} pin={pin}")
        else:
            failures.append(record)
            print(f"  FAIL {v['full_name']}: {msg}")

    # Telegram confirmation
    summary = _format_apply_telegram(successes, failures, skipped)
    print()
    print(summary)
    if send_telegram:
        try:
            from dispatch_v2.telegram_utils import send_admin_alert
            ok = send_admin_alert(summary)
            print(f"\nTelegram send: {'OK' if ok else 'FAIL'}")
        except Exception as e:
            print(f"\nTelegram send: SKIPPED ({type(e).__name__}: {e})")
    return 0 if not failures else 1


def _format_apply_telegram(
    successes: List[Dict[str, Any]],
    failures: List[Dict[str, Any]],
    skipped: List[Dict[str, Any]],
) -> str:
    lines = [f"✅ Migracja zakończona — {len(successes)} kurierów dodanych", ""]
    if successes:
        lines.append("Successful:")
        for i, s in enumerate(successes, start=1):
            pin_part = f" PIN={s['pin']}" if s.get("pin") else " (PIN already present)"
            lines.append(
                f"{i}. {s['full_name']} -> cid={s['cid']}, {s['tier']}{pin_part}"
            )
        lines.append("")
    if failures:
        lines.append("Failed:")
        for f in failures:
            lines.append(f"- {f['full_name']}: {f['msg']}")
        lines.append("")
    if skipped:
        lines.append(f"Skipped lines: {len(skipped)}")
        lines.append("")
    pin_recipients = [s for s in successes if s.get("pin")]
    if pin_recipients:
        lines.append("Action items dla Adriana:")
        lines.append("1. Wyślij PIN-y kurierom (Telegram/SMS):")
        for s in pin_recipients:
            lines.append(f"   - {s['full_name']}: PIN {s['pin']}")
        lines.append("2. GPS app registration DEFER do TASK D czwartek")
        lines.append("")
    lines.append("Ziomek od następnego ticku widzi pełną pulę.")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# VERIFY (Krok 6)
# ---------------------------------------------------------------------------

def cmd_verify() -> int:
    """Krok 6: re-run audit, expect 0 unmapped + 0 partial."""
    schedule = _load_json(SCHEDULE_PATH)
    kurier_ids = _load_json(KURIER_IDS_PATH)
    courier_tiers = _load_json(COURIER_TIERS_PATH)
    kurier_piny = _load_json(KURIER_PINY_PATH)

    buckets = _build_audit(schedule, kurier_ids, courier_tiers, kurier_piny)
    print(f"VERIFY: mapped={len(buckets['mapped'])} "
          f"partial={len(buckets['partial'])} "
          f"unmapped={len(buckets['unmapped'])}")
    if buckets["unmapped"]:
        print("STILL UNMAPPED:")
        for r in buckets["unmapped"]:
            print(f"  - {r['full_name']}")
    if buckets["partial"]:
        print("STILL PARTIAL:")
        for r in buckets["partial"]:
            print(f"  - {r['full_name']} missing={r['missing']}")
    return 0 if not (buckets["unmapped"] or buckets["partial"]) else 1


# ---------------------------------------------------------------------------
# Entrypoint
# ---------------------------------------------------------------------------

def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="ONE-SHOT migracja kurierów (Sprawa #1 2026-05-05)."
    )
    sub = parser.add_subparsers(dest="cmd", required=False)

    p_audit = sub.add_parser("audit", help="Krok 1 audit")
    p_audit.add_argument("--no-telegram", action="store_true",
                         help="Tylko console, bez Telegram DM")

    p_apply = sub.add_parser("apply", help="Krok 4 atomic migrate")
    p_apply.add_argument("input_file", help="Path do pliku z odpowiedzią Adriana")
    p_apply.add_argument("--no-telegram", action="store_true")
    p_apply.add_argument("--dry-run", action="store_true",
                         help="Parse + validate, NIE pisze do storów")

    sub.add_parser("verify", help="Krok 6 post-migration check")

    # Backwards-compatible flag form (--audit / --apply / --verify)
    parser.add_argument("--audit", action="store_true", help="alias for `audit`")
    parser.add_argument("--apply", metavar="INPUT_FILE",
                        help="alias for `apply <file>`")
    parser.add_argument("--verify", action="store_true", help="alias for `verify`")
    parser.add_argument("--no-telegram", action="store_true")
    parser.add_argument("--dry-run", action="store_true")

    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s %(message)s")

    if args.cmd == "audit" or args.audit:
        return cmd_audit(send_telegram=not args.no_telegram)
    if args.cmd == "apply" or args.apply:
        input_file = args.apply if args.apply else args.input_file
        return cmd_apply(input_file=input_file,
                         send_telegram=not args.no_telegram,
                         dry_run=args.dry_run)
    if args.cmd == "verify" or args.verify:
        return cmd_verify()

    parser.print_help()
    return 2


if __name__ == "__main__":
    sys.exit(main())
