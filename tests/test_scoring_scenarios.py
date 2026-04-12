"""
Test scoringu na 3 scenariuszach:
1. IDEALNY: kurier pusty, blisko restauracji, swieza zmiana
2. GRANICZNY: kurier z 2 paczkami, 2 km do restauracji, kat 90 stopni, 18 min w bagu
3. WASILKOW: kurier w centrum, restauracja w Wasilkowie (~12 km), 3 paczki w bagu w centrum
"""
import sys, json
sys.path.insert(0, '/root/.openclaw/workspace/scripts')
from dispatch_v2.scoring import score_candidate

BIALYSTOK_CENTER = (53.1325, 23.1688)
WASILKOW = (53.202, 23.205)

def hdr(title):
    print(f"\n{'='*70}\n{title}\n{'='*70}")

# Scenariusz 1: IDEALNY
hdr("SCENARIUSZ 1: IDEALNY")
courier = (53.130, 23.160)          # centrum
restaurant = (53.132, 23.165)       # ~400 m dalej, Rynek Kosciuszki
feas = check_feasibility(courier, restaurant, bag_drop_coords=None, bag_size=0)
print(f"Feasibility: {feas[0]} ({feas[1]})  metrics={feas[2]}")
sc = score_candidate(courier, restaurant, bag_drop_coords=None, bag_size=0, oldest_in_bag_min=None)
print(f"Scoring: {sc['reasoning']}")
print(f"Total: {sc['total']} (oczekiwane >85, wszystko max)")
assert feas[0] == "MAYBE", f"Oczekiwano MAYBE, dostano {feas[0]}"
assert sc['total'] > 85, f"Oczekiwano >85, dostano {sc['total']}"
print("✅ PASS")

# Scenariusz 2: GRANICZNY
hdr("SCENARIUSZ 2: GRANICZNY")
courier = (53.130, 23.160)
bag_drops = [(53.115, 23.155), (53.118, 23.150)]  # paczki na poludnie
restaurant = (53.145, 23.170)                      # nowa restauracja na polnoc (~1.7 km), kat ~150 stopni (zawrot)
feas = check_feasibility(courier, restaurant, bag_drop_coords=bag_drops, bag_size=2)
print(f"Feasibility: {feas[0]} ({feas[1]})  metrics={feas[2]}")
# Moze byc NO (zawrot >120) albo MAYBE jesli kat jest mniejszy - liczymy realnie
sc = score_candidate(courier, restaurant, bag_drop_coords=bag_drops, bag_size=2, oldest_in_bag_min=18)
print(f"Scoring: {sc['reasoning']}")
print(f"Total: {sc['total']}")
if feas[0] == "NO":
    print("✅ PASS (feasibility odrzucil zawrot - poprawnie)")
else:
    assert 30 < sc['total'] < 70, f"Graniczny powinien byc 30-70, dostano {sc['total']}"
    print(f"✅ PASS (MAYBE, total graniczny)")

# Scenariusz 3: WASILKOW - MUSI ODRZUCIC
hdr("SCENARIUSZ 3: WASILKOW (musi odrzucic)")
courier = BIALYSTOK_CENTER
bag_drops = [(53.128, 23.160), (53.135, 23.165), (53.125, 23.170)]  # 3 paczki w centrum
restaurant = WASILKOW
feas = check_feasibility(courier, restaurant, bag_drop_coords=bag_drops, bag_size=3)
print(f"Feasibility: {feas[0]} ({feas[1]})  metrics={feas[2]}")
assert feas[0] == "NO", f"Wasilkow MUSI byc NO, dostano {feas[0]}"
print("✅ PASS (odrzucony - {})".format(feas[1]))

# Dodatkowo: direction angle = 180° → kierunek=0 pkt
hdr("BONUS: test kierunku 180 stopni")
courier = (53.130, 23.160)
bag_drops = [(53.110, 23.160)]   # paczka daleko na poludnie
restaurant = (53.150, 23.160)    # restauracja daleko na polnoc = 180° zawrot
feas = check_feasibility(courier, restaurant, bag_drop_coords=bag_drops, bag_size=1)
print(f"Feasibility: {feas[0]} ({feas[1]})")
assert feas[0] == "NO", "180 stopni MUSI byc NO"
print("✅ PASS")

# Bonus: czas 35 min = time_penalty 100
hdr("BONUS: test time_penalty dla 35 min w bagu")
sc = score_candidate((53.13, 23.16), (53.135, 23.165), bag_drop_coords=None, bag_size=1, oldest_in_bag_min=35)
print(f"Scoring (35 min w bagu): czas={sc['components']['czas']} (oczekiwane 0)")
assert sc['components']['czas'] == 0.0, f"35 min = 0 pkt, dostano {sc['components']['czas']}"
print("✅ PASS")

# Bonus: czas 20 min = time_penalty 0
sc = score_candidate((53.13, 23.16), (53.135, 23.165), bag_drop_coords=None, bag_size=1, oldest_in_bag_min=20)
print(f"Scoring (20 min w bagu): czas={sc['components']['czas']} (oczekiwane 100)")
assert sc['components']['czas'] == 100.0
print("✅ PASS")

print("\n" + "="*70)
print("WSZYSTKIE TESTY ✅ PASS")
print("="*70)
