"""V3.19h BUG-4: generator courier_tiers.json z ground truth.

Jednorazowe narzędzie do regeneracji tier ground truth. Run po:
  - zmianie listy kurierów (nowi / odeszli)
  - kwartalnym refresh bag stats (re-run V3.19g wave analysis)
  - override_manual changes od właściciela

Input:
  - /tmp/v319g_courier_tiers_preview.json (z V3.19g discovery, 37 eligible)
  - /root/.openclaw/workspace/dispatch_state/kurier_ids.json (cid mapping)
  - hardcoded tier ground truth od właściciela 2026-04-20 (CONTEXT PACK)

Output:
  - /root/.openclaw/workspace/dispatch_state/courier_tiers.json (atomic write)

Usage:
  cd /root/.openclaw/workspace/scripts
  python3 -m dispatch_v2.build_v319h_courier_tiers
"""
import json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path


# Ground truth od właściciela 2026-04-20 (CONTEXT PACK).
TIER_GROUND_TRUTH = {
    # Gold: Bartek O. (123), Mateusz O (413), Krystian (61), Gabriel (179)
    "123": "gold",   # Bartek O.
    "413": "gold",   # Mateusz O
    "61":  "gold",   # Krystian
    "179": "gold",   # Gabriel [cap=4 override]
    # Standard+: Adrian R (400)
    "400": "std+",   # Adrian R
    # Slow: Artsem Km (504), Łukasz B (511), Michał Li (508)
    "504": "slow",   # Artsem Km
    "511": "slow",   # Łukasz B
    "508": "slow",   # Michał Li
}
# Wszyscy inni z kurier_ids → "std" (default).

# Per-cid override (właściciel caveat: Gabriel cap=4, "quality drops above 4").
CAP_OVERRIDE = {
    "179": {
        "peak": 4, "normal": 4, "off_peak": 3,
        "reason": "quality drops above 4 — owner note 2026-04-20",
    },
}

V319G_PREVIEW_PATH = "/tmp/v319g_courier_tiers_preview.json"
KURIER_IDS_PATH = "/root/.openclaw/workspace/dispatch_state/kurier_ids.json"
OUTPUT_PATH = "/root/.openclaw/workspace/dispatch_state/courier_tiers.json"


def _atomic_write(path, data):
    """Atomic write: temp → fsync → rename."""
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp_fd, tmp_path = tempfile.mkstemp(
        dir=str(p.parent), prefix=".tmp_", suffix=".json"
    )
    try:
        with os.fdopen(tmp_fd, "w") as f:
            json.dump(data, f, indent=2, ensure_ascii=False, default=str)
            f.flush()
            os.fsync(f.fileno())
        os.rename(tmp_path, path)
    except Exception:
        if os.path.exists(tmp_path):
            os.unlink(tmp_path)
        raise


def build():
    # Load V3.19g preview (keyed po cid, z wave analysis stats).
    v319g_preview = {}
    try:
        with open(V319G_PREVIEW_PATH) as f:
            v319g_preview = json.load(f)
        print(f"Loaded V3.19g preview: {len(v319g_preview)} couriers keyed by cid")
    except (FileNotFoundError, json.JSONDecodeError) as e:
        print(f"WARN: V3.19g preview unavailable ({e}) — emit tier-only entries")

    # Load kurier_ids (wszystkich active couriers)
    with open(KURIER_IDS_PATH) as f:
        kurier_ids = json.load(f)
    all_cids = {str(c) for c in kurier_ids.values()}
    print(f"Loaded kurier_ids: {len(all_cids)} total cids")

    # Build per-cid entry
    out = {
        "_meta": {
            "schema_version": "v1",
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "source": (
                "ground_truth_owner_2026-04-20 + V3.19g 6mo analysis "
                "(/tmp/v319g_courier_tiers_preview.json)"
            ),
            "eligibility_min_waves_for_stats": 50,
            "tier_ground_truth_cids": TIER_GROUND_TRUTH,
            "cap_override_cids": list(CAP_OVERRIDE.keys()),
        }
    }
    # Reverse name lookup dla labelów
    cid_to_name = {str(c): n for n, c in kurier_ids.items()}

    for cid in sorted(all_cids, key=lambda c: int(c) if c.isdigit() else 9999):
        # Skip Koordynator (cid=26)
        if cid == "26":
            continue
        tier = TIER_GROUND_TRUTH.get(cid, "std")
        entry = {
            "name": cid_to_name.get(cid, "?"),
            "bag": {
                "tier": tier,
                "cap_override": CAP_OVERRIDE.get(cid),
            },
        }
        # Enrich z V3.19g stats jeśli dostępne
        stats = v319g_preview.get(cid)
        if stats:
            bag_stats = stats.get("bag", {})
            entry["bag"].update({
                "orders_per_wave_p50": bag_stats.get("orders_per_wave_p50"),
                "orders_per_wave_p90": bag_stats.get("orders_per_wave_p90"),
                "orders_per_wave_p99": bag_stats.get("orders_per_wave_p99"),
                "max_concurrent_observed": bag_stats.get("max_concurrent"),
                "bag_time_p90_min": bag_stats.get("bag_time_p90_min"),
            })
            entry["speed"] = stats.get("speed")
            entry["bundle"] = stats.get("bundle")
        out[cid] = entry

    _atomic_write(OUTPUT_PATH, out)
    print(f"Wrote {OUTPUT_PATH} ({len(out) - 1} couriers; _meta + {len(out) - 1} entries)")


if __name__ == "__main__":
    build()
