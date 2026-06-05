"""BUG-2 Front C (2026-06-05) — bootstrap whitelist un-mask + różne-nazwy guard.

Symptom: Raj(96) i Grill Kebab(138) miały bajt-w-bajt identyczne pickup_coords,
ale były na HARD/SOFT whiteliscie → walidator bootstrapu CICHO akceptował kolizję.
Fix: usuń 96 z HARD whitelist (identyczne koordy znów alarmują), guard wypisuje
pary o RÓŻNYCH nazwach pod identycznymi koordami. {96,138} po korekcie adresu są
~29m (legit SOFT — sąsiednie, różne budynki) → zostają jako SOFT whitelist.

Runs as standalone Python script.
"""
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_SCRIPTS = os.path.abspath(os.path.join(_HERE, "..", ".."))
if _SCRIPTS not in sys.path:
    sys.path.insert(0, _SCRIPTS)

from dispatch_v2 import bootstrap_restaurants as br

passed = 0
failed = 0


def check(label: str, cond: bool):
    global passed, failed
    if cond:
        passed += 1
        print(f"  OK {passed}. {label}")
    else:
        failed += 1
        print(f"  FAIL {passed + failed}. {label}")


def _r(company, lat, lng):
    return {"company": company, "lat": lat, "lng": lng}


# ── Whitelist invariants ─────────────────────────────────────
check("96 usunięty z WSZYSTKICH HARD whitelist setów",
      not any(96 in wl for wl in br.HARD_DUPLICATE_WHITELIST))
check("zły SOFT set {53,96,138} usunięty",
      frozenset({53, 96, 138}) not in br.SOFT_DUPLICATE_WHITELIST)
check("legit SOFT {96,138} (~29m, sąsiednie) obecny",
      frozenset({96, 138}) in br.SOFT_DUPLICATE_WHITELIST)
check("HARD {114,138} (~72m, ta sama komórka 3-dec) zachowany",
      any(frozenset({114, 138}).issubset(wl) for wl in br.HARD_DUPLICATE_WHITELIST))

# ── _norm_name ───────────────────────────────────────────────
check("_norm_name: case/whitespace-insensitive równość",
      br._norm_name("  Raj  ") == br._norm_name("raj"))
check("_norm_name: różne nazwy != ",
      br._norm_name("Raj") != br._norm_name("Grill Kebab"))

# ── HARD un-mask: 96/138 przy IDENTYCZNYCH koordach (regresja) ──
# Klucze INT — tak jak buduje je geocode_all (results[aid] gdzie aid=int(aid_str)).
res_collide = {
    96: _r("Raj", 53.132464, 23.165517),
    138: _r("Grill Kebab", 53.132464, 23.165517),
}
hard, soft, outliers, low = br.validate(res_collide)
check("identyczne koordy 96/138 wykryte jako HARD duplikat", len(hard) == 1)
hp = hard[0]
check("96/138 NIE whitelisted (alarm odmaskowany)", hp[4] is False)
check("guard: pary mają RÓŻNE nazwy", br._norm_name(hp[1]) != br._norm_name(hp[3]))
blocking = br.report(res_collide, [], hard, soft, outliers, low)
check("non-whitelisted HARD dup → report blokuje (blocking=True)", blocking is True)

# ── 114/138 nadal whitelisted (legit pasaż, ta sama komórka 3-dec) ──
res_legit = {
    114: _r("350 Stopni", 53.132169, 23.166483),
    138: _r("Grill Kebab", 53.132464, 23.165517),
}
hard2, _s2, _o2, _l2 = br.validate(res_legit)
check("114/138 wykryte jako HARD (rounding 3-dec)", len(hard2) == 1)
check("114/138 whitelisted (is_wl=True) → nie blokuje", hard2[0][4] is True)

# ── Po korekcie: 96(Kil.13)/138(Kil.12) ~29m = SOFT, whitelisted, brak HARD ──
res_fixed = {
    96: _r("Raj", 53.1322335, 23.1653257),       # Kilińskiego 13 (poprawione)
    138: _r("Grill Kebab", 53.132464, 23.165517),  # Kilińskiego 12
}
hard3, soft3, _o3, _l3 = br.validate(res_fixed)
check("po korekcie: brak HARD dup 96/138", len(hard3) == 0)
check("po korekcie: 96/138 to SOFT (<50m)", len(soft3) == 1)
check("po korekcie: SOFT 96/138 whitelisted (nie blokuje)", br.whitelisted(soft3[0]) is True)

# ============================================================
total = passed + failed
print()
print("=" * 60)
print(f"BUG-2 BOOTSTRAP GUARD: {passed}/{total} PASS")
print("=" * 60)

if failed:
    sys.exit(1)
