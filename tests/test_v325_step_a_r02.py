"""V3.25 STEP A R-02 full — atomic regression suite.

Covers:
- A.2 name resolution refactor (5 lokalizacji _load_courier_names + inverse kurier_ids fallback)
- A.3 match_courier_strict z learning_log alarms
- A.4 PANEL_TO_SCHEDULE updates (Jakub OL, Szymon Sa, Grzegorz, Mykyta K, Krystian)
- A.5 courier_tiers updates (Jakub OL std+, Krystian inactive, Mykyta K inactive,
       Szymon Sa new, Grzegorz Rogowski new)
- A.7 kurier_ids alias-pair (Szymon Sa/Szymon Sadowski → 522, Grzegorz R/Grzegorz Rogowski → 500)

Tests run against PRODUCTION dispatch_state files (read-only). Apply phase
is responsible for ensuring files are in expected state przed test run.
"""
import importlib
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
sys.path.insert(0, str(Path(__file__).resolve().parents[3]))  # scripts/

from dispatch_v2 import courier_resolver, courier_ranking, telegram_approver, sla_tracker
from dispatch_v2 import manual_overrides as mo
import schedule_utils as su


def main():
    results = {"pass": 0, "fail": 0}

    def expect(label, cond, detail=""):
        if cond:
            print(f"  ✅ {label}")
            results["pass"] += 1
        else:
            print(f"  ❌ {label}  {detail}")
            results["fail"] += 1

    # --- A.7 kurier_ids alias-pair ---
    print("\n=== A.7 kurier_ids alias-pair ===")
    with open('/root/.openclaw/workspace/dispatch_state/kurier_ids.json') as f:
        ids = json.load(f)
    expect("'Szymon Sa' → 522", ids.get('Szymon Sa') == 522, f"got {ids.get('Szymon Sa')}")
    expect("'Szymon Sadowski' → 522", ids.get('Szymon Sadowski') == 522, f"got {ids.get('Szymon Sadowski')}")
    expect("'Grzegorz R' → 500", ids.get('Grzegorz R') == 500)
    expect("'Grzegorz Rogowski' → 500", ids.get('Grzegorz Rogowski') == 500)
    expect("'Grzegorz' → 500 (backward compat zachowane)", ids.get('Grzegorz') == 500)
    expect("'Albert Dec' → 414 (zachowane z 21.04)", ids.get('Albert Dec') == 414)
    expect("'Jakub OL' → 370 (Q2=a no rename)", ids.get('Jakub OL') == 370)

    # --- A.2 name resolution refactor (all 5 modules return Albert Dec via inverse) ---
    print("\n=== A.2 _load_courier_names — inverse kurier_ids fallback ===")
    for mod_name, mod in [('courier_resolver', courier_resolver),
                          ('courier_ranking', courier_ranking),
                          ('sla_tracker', sla_tracker)]:
        importlib.reload(mod)
        n = mod._load_courier_names()
        expect(f"{mod_name}: cid=414 → 'Albert Dec' (z inverse kurier_ids)",
               n.get('414') == 'Albert Dec', f"got {n.get('414')!r}")
        expect(f"{mod_name}: cid=522 → 'Szymon Sa' (z courier_names higher-prio)",
               n.get('522') == 'Szymon Sa', f"got {n.get('522')!r}")
        expect(f"{mod_name}: cid=393 → 'Michał K.' (existing, unchanged)",
               n.get('393') == 'Michał K.')
        expect(f"{mod_name}: cid=9279 NIE w merged (post-hotfix cleanup)",
               '9279' not in n, f"keys with 9279: {[k for k in n if '9279' in k]}")

    # telegram_approver has caching — reset cache first
    telegram_approver._courier_names_cache = None
    n = telegram_approver._load_courier_names()
    expect("telegram_approver: cid=414 → 'Albert Dec'", n.get('414') == 'Albert Dec')

    # manual_overrides._load_names returns LIST
    importlib.reload(mo)
    names_list = mo._load_names()
    expect("manual_overrides: 'Albert Dec' w liście names (z inverse fallback)",
           'Albert Dec' in names_list, f"sample: {names_list[:5]}")
    expect("manual_overrides: 'Szymon Sa' w liście (z courier_names)",
           'Szymon Sa' in names_list)

    # --- A.3 match_courier_strict ---
    print("\n=== A.3 match_courier_strict ===")
    importlib.reload(su)
    schedule = su.load_schedule()
    expect("match('Jakub OL') → 'Kuba Olchowik' (PANEL_TO_SCHEDULE)",
           su.match_courier_strict('Jakub OL', schedule) == 'Kuba Olchowik')
    expect("match('Szymon Sa') → 'Szymon Sadowski' (PANEL_TO_SCHEDULE)",
           su.match_courier_strict('Szymon Sa', schedule) == 'Szymon Sadowski')
    expect("match('Grzegorz') → 'Grzegorz Rogowski' (PANEL_TO_SCHEDULE update from None)",
           su.match_courier_strict('Grzegorz', schedule) == 'Grzegorz Rogowski')
    expect("match('Grzegorz R') → 'Grzegorz Rogowski' (alias)",
           su.match_courier_strict('Grzegorz R', schedule) == 'Grzegorz Rogowski')
    expect("match('Mykyta K') → None (ex-courier explicit None)",
           su.match_courier_strict('Mykyta K', schedule) is None)
    expect("match('Krystian') → None (ex-courier explicit None)",
           su.match_courier_strict('Krystian', schedule) is None)
    expect("match('xyz123nonsense') → None",
           su.match_courier_strict('xyz123nonsense', schedule) is None)

    # is_on_shift uses match_courier_strict
    on, reason = su.is_on_shift('Mykyta K', schedule)
    expect("is_on_shift('Mykyta K') → True (no schedule match — fall-through legacy)",
           on, f"reason={reason}")
    on, reason = su.is_on_shift('Jakub OL', schedule)
    # Kuba Olchowik may or may not be working — just verify match worked (returned valid full name)
    expect("is_on_shift('Jakub OL') → uses Kuba Olchowik schedule entry",
           reason and ('Kuba Olchowik' in reason or 'jeszcze' in reason or 'zmiana' in reason
                      or 'pracuje' in reason or 'dziś' in reason),
           f"reason={reason!r}")

    # --- A.5 courier_tiers updates ---
    print("\n=== A.5 courier_tiers ===")
    with open('/root/.openclaw/workspace/dispatch_state/courier_tiers.json') as f:
        tiers = json.load(f)
    expect("cid=370 (Jakub OL): bag.tier='std+'",
           (tiers.get('370') or {}).get('bag', {}).get('tier') == 'std+',
           f"got {(tiers.get('370') or {}).get('bag', {}).get('tier')!r}")
    expect("cid=61 (Krystian): inactive=true",
           (tiers.get('61') or {}).get('inactive') is True)
    expect("cid=426 (Mykyta K): inactive=true",
           (tiers.get('426') or {}).get('inactive') is True)
    expect("cid=522 (Szymon Sa): NEW entry tier='new'",
           (tiers.get('522') or {}).get('bag', {}).get('tier') == 'new')
    expect("cid=522: cap_override peak=2",
           ((tiers.get('522') or {}).get('bag', {}).get('cap_override') or {}).get('peak') == 2)
    expect("cid=522: tier_label='new'",
           (tiers.get('522') or {}).get('tier_label') == 'new')
    expect("cid=500 (Grzegorz R): tier='new'",
           (tiers.get('500') or {}).get('bag', {}).get('tier') == 'new')
    expect("cid=500: tier_label='new'",
           (tiers.get('500') or {}).get('tier_label') == 'new')

    # --- A.6 PIN gen verification ---
    print("\n=== A.6 PINs ===")
    with open('/root/.openclaw/workspace/dispatch_state/kurier_piny.json') as f:
        piny = json.load(f)
    expect("PIN 1187 → Szymon Sa", piny.get('1187') == 'Szymon Sa')
    expect("PIN 5139 → Grzegorz R", piny.get('5139') == 'Grzegorz R')
    expect("PIN total: 41 (39 base + 2 nowe)", len(piny) == 41)

    # --- Integration: fleet snapshot ma Alberta z name + nie ma 9279 ---
    print("\n=== Integration: fleet snapshot post-A.2 ===")
    importlib.reload(courier_resolver)
    fleet = courier_resolver.build_fleet_snapshot()
    expect("fleet contains cid=414 z name='Albert Dec' (H1 fix)",
           fleet.get('414') and fleet['414'].name == 'Albert Dec',
           f"got {fleet.get('414') and fleet['414'].name!r}")
    expect("fleet contains cid=522 z name='Szymon Sa' (z courier_names)",
           fleet.get('522') and fleet['522'].name == 'Szymon Sa',
           f"got {fleet.get('522') and fleet['522'].name!r}")
    expect("fleet contains cid=500 z name='Grzegorz' (zachowane)",
           fleet.get('500') and fleet['500'].name == 'Grzegorz',
           f"got {fleet.get('500') and fleet['500'].name!r}")
    expect("fleet NIE zawiera cid=9279 (regression hotfix)",
           '9279' not in fleet)

    print(f"\n=== summary: {results['pass']} pass, {results['fail']} fail ===")
    return 0 if results["fail"] == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
