"""V3.26 STEP 5 (R-06 MULTI-STOP-TRAJECTORY) — 12 cases per Adrian spec.

Tests classify_trajectory function (district-based) + integration:
- T1 SAME (+40): drop ZW, pickup ZW
- T2 SIMILAR via adjacency (+15): drop ZW, pickup Starosielce
- T3 SIMILAR cross-list (+15): drop Sienkiewicza, pickup Bojary
- T4 OPPOSITE W-E (-40): drop ZW, pickup Sienkiewicza
- T5 OPPOSITE N-SW (-40): drop Antoniuk, pickup Kawaleryjskie
- T6 SIDEWAYS W-SW (-10): drop ZW, pickup Kawaleryjskie
- T7 CENTER neutrality: drop Centrum, pickup Antoniuk → SIDEWAYS (-10)
- T8 bag size 1 → SKIPPED (R-06 fires tylko bag>=2)
- T9 no_gps pos_source → SKIPPED
- T10 flag OFF → no R-06 fires
- T11 Unknown district → no bonus (UNKNOWN classification)
- T12 NEW outside zones (Olmonty SE → Antoniuk N) → OPPOSITE -40
"""
import importlib
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from dispatch_v2 import common, dispatch_pipeline  # noqa: E402
from dispatch_v2.districts_data import classify_trajectory  # noqa: E402


class _MockCandidate:
    def __init__(self, courier_id, name, score, bag_size_before=2,
                 pos_source='last_picked_up_delivery', bag_context=None):
        self.courier_id = courier_id
        self.name = name
        self.score = score
        self.metrics = {
            "bag_size_before": bag_size_before,
            "pos_source": pos_source,
            "bag_context": bag_context or [],
        }


class _MockOrder:
    def __init__(self, restaurant=None):
        self.restaurant = restaurant


def main():
    results = {"pass": 0, "fail": 0}

    def expect(label, cond, detail=""):
        if cond:
            print(f"  ✅ {label}")
            results["pass"] += 1
        else:
            print(f"  ❌ {label}  {detail}")
            results["fail"] += 1

    importlib.reload(common)
    importlib.reload(dispatch_pipeline)
    adj = common.BIALYSTOK_DISTRICT_ADJACENCY

    # --------- Pure classifier tests ---------
    print("\n=== T1 SAME: drop ZW, pickup ZW → SAME ===")
    r, _ = classify_trajectory('Zielone Wzgórza', 'Zielone Wzgórza', adj)
    expect("T1 SAME", r == 'SAME', f"got {r}")

    print("\n=== T2 SIMILAR via adjacency: ZW → Starosielce ===")
    r, _ = classify_trajectory('Zielone Wzgórza', 'Starosielce', adj)
    expect("T2 SIMILAR", r == 'SIMILAR', f"got {r}")

    print("\n=== T3 SIMILAR cross-list: Sienkiewicza → Bojary ===")
    r, _ = classify_trajectory('Sienkiewicza', 'Bojary', adj)
    expect("T3 SIMILAR (Sienkiewicza-Bojary adjacency)", r == 'SIMILAR', f"got {r}")

    print("\n=== T4 OPPOSITE W-E: ZW → Sienkiewicza ===")
    r, _ = classify_trajectory('Zielone Wzgórza', 'Sienkiewicza', adj)
    expect("T4 OPPOSITE", r == 'OPPOSITE', f"got {r}")

    print("\n=== T5 OPPOSITE N-SW: Antoniuk → Kawaleryjskie ===")
    r, _ = classify_trajectory('Antoniuk', 'Kawaleryjskie', adj)
    expect("T5 OPPOSITE (N↔SW)", r == 'OPPOSITE', f"got {r}")

    print("\n=== T6 SIDEWAYS W-SW: ZW → Kawaleryjskie ===")
    r, _ = classify_trajectory('Zielone Wzgórza', 'Kawaleryjskie', adj)
    expect("T6 SIDEWAYS (W↔SW)", r == 'SIDEWAYS', f"got {r}")

    print("\n=== T7 CENTER touch: Centrum → Antoniuk ===")
    r, detail = classify_trajectory('Centrum', 'Antoniuk', adj)
    expect("T7 CENTER (no Antoniuk adj) → SIDEWAYS",
           r == 'SIDEWAYS', f"got {r} ({detail})")
    r, _ = classify_trajectory('Centrum', 'Bojary', adj)
    expect("T7b CENTER (Bojary IS adjacent) → SIMILAR", r == 'SIMILAR', f"got {r}")

    print("\n=== T11 Unknown district → UNKNOWN ===")
    r, _ = classify_trajectory('Unknown', 'Centrum', adj)
    expect("T11 'Unknown' drop → UNKNOWN", r == 'UNKNOWN')
    r, _ = classify_trajectory('Centrum', None, adj)
    expect("T11b None pickup → UNKNOWN", r == 'UNKNOWN')

    print("\n=== T12 NEW outside zones ===")
    r, _ = classify_trajectory('Olmonty', 'Antoniuk', adj)
    expect("T12 Olmonty (SE) ↔ Antoniuk (N) → OPPOSITE", r == 'OPPOSITE')
    r, _ = classify_trajectory('Izabelin', 'Bacieczki', adj)
    expect("T12b Izabelin (SE) ↔ Bacieczki (N) → OPPOSITE", r == 'OPPOSITE')
    r, _ = classify_trajectory('Wasilków', 'Zielone Wzgórza', adj)
    expect("T12c Wasilków (E NEW) ↔ ZW (W) → OPPOSITE", r == 'OPPOSITE')

    # --------- Pipeline integration tests ---------
    print("\n=== T8 bag_size 1 → SKIPPED ===")
    os.environ["ENABLE_V326_MULTISTOP_TRAJECTORY"] = "1"
    importlib.reload(common)
    importlib.reload(dispatch_pipeline)
    bag_ctx = [{"order_id": "o1", "restaurant": "X", "delivery_address": "Wiosenna 5"}]
    cand = _MockCandidate('123', 'Bartek', 100.0, bag_size_before=1, bag_context=bag_ctx)
    new_order = _MockOrder(restaurant="Mama Thai Bistro")
    out = dispatch_pipeline._v326_multistop_trajectory([cand], new_order, "t8")
    expect("T8 bag=1 → score unchanged 100", out[0].score == 100.0,
           f"got {out[0].score}")
    expect("T8 skip_reason includes 'bag=1'",
           "bag=1" in (out[0].metrics.get("v326_r06_skip_reason") or ""),
           f"got {out[0].metrics.get('v326_r06_skip_reason')!r}")

    print("\n=== T9 pos_source=no_gps → SKIPPED ===")
    cand = _MockCandidate('124', 'X', 100.0, bag_size_before=2,
                          pos_source='no_gps', bag_context=bag_ctx)
    out = dispatch_pipeline._v326_multistop_trajectory([cand], new_order, "t9")
    expect("T9 no_gps → score unchanged", out[0].score == 100.0)
    expect("T9 skip_reason == 'no_gps'",
           out[0].metrics.get("v326_r06_skip_reason") == "no_gps")

    print("\n=== T10 flag OFF → no fires ===")
    os.environ["ENABLE_V326_MULTISTOP_TRAJECTORY"] = "0"
    importlib.reload(common)
    importlib.reload(dispatch_pipeline)
    cand = _MockCandidate('125', 'Z', 100.0, bag_size_before=2, bag_context=bag_ctx)
    out = dispatch_pipeline._v326_multistop_trajectory([cand], new_order, "t10")
    expect("T10 flag OFF → unchanged 100", out[0].score == 100.0)
    expect("T10 metrics NIE ma v326_r06_relation",
           "v326_r06_relation" not in out[0].metrics)

    # Cleanup
    del os.environ["ENABLE_V326_MULTISTOP_TRAJECTORY"]
    importlib.reload(common)
    importlib.reload(dispatch_pipeline)

    print(f"\n=== summary: {results['pass']} pass, {results['fail']} fail ===")
    return 0 if results["fail"] == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
