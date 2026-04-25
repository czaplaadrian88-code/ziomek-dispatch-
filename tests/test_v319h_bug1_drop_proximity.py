"""V3.19h BUG-1 drop_proximity_factor — unit tests.

Coverage:
  1. Adjacency symmetry test (GUARDRAIL pre-commit)
  2. drop_zone_from_address: Białystok streets, outside-city, Unknown
  3. drop_proximity_factor: same / adjacent / distant / Unknown
  4. Flag gate: False → no adjustment; True → mnożnik
  5. Max bonus stack warning (Q3 GUARDRAIL) z uwzględnieniem BUG-1
"""
import sys
import os

_HERE = os.path.dirname(os.path.abspath(__file__))
_SCRIPTS = os.path.abspath(os.path.join(_HERE, "..", ".."))
if _SCRIPTS not in sys.path:
    sys.path.insert(0, _SCRIPTS)

from dispatch_v2 import common as C

passed = 0
failed = 0


def check(label, cond, detail=""):
    global passed, failed
    if cond:
        passed += 1
        print(f"  OK {passed}. {label}")
    else:
        failed += 1
        print(f"  FAIL {passed + failed}. {label} {detail}")


# ============================================================
print("=== V3.19h BUG-1: adjacency SYMMETRY (pre-commit GUARDRAIL) ===")
# ============================================================

adj = C.BIALYSTOK_DISTRICT_ADJACENCY
asymmetric_pairs = []
for a, neighbors in adj.items():
    for b in neighbors:
        if a not in adj.get(b, set()):
            asymmetric_pairs.append((a, b))

check("1. adjacency map SYMMETRIC (a→b implies b→a)",
      len(asymmetric_pairs) == 0,
      detail=f"asymmetric pairs: {asymmetric_pairs[:5]}")

# Każdy district wymieniony jako neighbour musi też mieć swój klucz w dict
missing_keys = set()
for a, neighbors in adj.items():
    for b in neighbors:
        if b not in adj:
            missing_keys.add(b)
check("2. wszystkie neighbour districts mają klucz w adjacency dict",
      len(missing_keys) == 0,
      detail=f"missing: {missing_keys}")

# ============================================================
print("\n=== V3.19h BUG-1: drop_zone_from_address ===")
# ============================================================

cases = [
    # Białystok core streets
    (("Lipowa 12", "Białystok"), "Centrum"),
    (("Rynek Kościuszki 32", "Białystok"), "Centrum"),
    (("Waszyngtona 24/137", "Białystok"), "Piaski"),
    (("Kraszewskiego 17c/24", "Białystok"), "Bojary"),
    # Outside-city via city field
    (("Fabryczna 5", "Choroszcz"), "Choroszcz"),
    (("Warszawska 10", "Wasilków"), "Wasilków"),
    (("Modrzewiowa 5", "Kleosin"), "Kleosin"),
    (("Cisowa 3", "Ignatki-osiedle"), "Ignatki-osiedle"),
    # Unknown fallback
    (("Abc Street 99", "Białystok"), "Unknown"),
    (("Nothing", "SomeVillage"), "Unknown"),  # other city
    # Empty / None
    ((None, None), "Unknown"),
    (("", ""), "Unknown"),
]

for i, ((addr, city), expected) in enumerate(cases, start=3):
    result = C.drop_zone_from_address(addr, city)
    check(f"{i}. drop_zone({addr!r}, {city!r}) → {expected}",
          result == expected, detail=f"got {result!r}")

# ============================================================
print("\n=== V3.19h BUG-1: drop_proximity_factor ===")
# ============================================================

pf_cases = [
    (("Centrum", "Centrum"), 1.0),           # same zone
    (("Centrum", "Piaski"), 0.5),            # adjacent
    (("Centrum", "Bojary"), 0.5),            # adjacent
    (("Centrum", "Wasilków"), 0.0),          # distant (outside-city)
    (("Centrum", "Dojlidy Górne"), 0.0),     # distant
    (("Unknown", "Centrum"), 0.0),           # Unknown
    (("Centrum", "Unknown"), 0.0),
    (("Dziesięciny I", "Dziesięciny II"), 0.5),  # adjacent cluster
    (("Piasta I", "Piasta II"), 0.5),
    (("Wasilków", "Jaroszówka"), 0.5),        # outside-city bridge
    (("Kleosin", "Ignatki-osiedle"), 0.5),    # outside-city adjacent
    (("Kleosin", "Wasilków"), 0.0),           # outside-city not adjacent
]

i_start = 3 + len(cases)
for i, ((z1, z2), expected) in enumerate(pf_cases, start=i_start):
    r = C.drop_proximity_factor(z1, z2)
    check(f"{i}. factor({z1!r}, {z2!r}) → {expected}",
          r == expected, detail=f"got {r}")

# ============================================================
print("\n=== V3.19h BUG-1: flag gate + constants ===")
# ============================================================

next_i = i_start + len(pf_cases)
check(f"{next_i}. flag default True (LIVE od V3.19h 2026-04-21)",
      C.ENABLE_V319H_BUG1_DROP_PROXIMITY_FACTOR is True)
check(f"{next_i + 1}. 28 official districts loaded",
      len(C.BIALYSTOK_DISTRICTS) == 28)
check(f"{next_i + 2}. adjacency ma 32 entries (28 official + 4 outside)",
      len(C.BIALYSTOK_DISTRICT_ADJACENCY) == 32)

# ============================================================
print("\n=== V3.19h BUG-1: max bonus stack (Q3 GUARDRAIL) ===")
# ============================================================

# Bug1 adjustment jest MULTIPLICATIVE na bonus_l1. Maksymalna wartość bonus_l1
# po adjustment = 25.0 (factor 1.0). Minimalna = 0.0 (factor 0.0).
# Więc BUG-1 nie zwiększa max stack, tylko potencjalnie zmniejsza bonus_l1.
# W połączeniu z BUG-4 (penalty-only) i BUG-2 (+30 bonus) — total stack zmiany
# w korzystnej kierunek dla Bartek-style + penalty dla Std slipshod SR.
# Sanity: factor w range [0, 1]
i_warn = next_i + 3
for _z1, _z2 in [('Centrum', 'Piaski'), ('Unknown', 'Unknown'), ('Choroszcz', 'Centrum')]:
    f = C.drop_proximity_factor(_z1, _z2)
    check(f"{i_warn}. factor({_z1}, {_z2})={f} w [0.0, 1.0]", 0.0 <= f <= 1.0)
    i_warn += 1

print("\n" + "=" * 60)
print(f"V3.19h BUG-1 DROP PROXIMITY: {passed}/{passed + failed} PASS"
      if failed == 0 else
      f"V3.19h BUG-1 DROP PROXIMITY: {passed}/{passed + failed} PASS, {failed} FAIL")
print("=" * 60)

if failed:
    sys.exit(1)
