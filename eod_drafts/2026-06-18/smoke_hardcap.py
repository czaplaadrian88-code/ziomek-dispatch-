#!/usr/bin/env python3
"""Smoke twardego capa: flaga ON/OFF × tier × wielkość worka."""
import sys; sys.path.insert(0,"/root/.openclaw/workspace/scripts")
import dispatch_v2.common as C
from dispatch_v2 import feasibility_v2 as F
from dispatch_v2.route_simulator_v2 import OrderSim
from datetime import datetime, timezone

PC=(53.132,23.159); DC=(53.140,23.150)
now=datetime(2026,6,18,12,0,tzinfo=timezone.utc)
def bag(n): return [OrderSim(f"o{i}",PC,DC,None,"assigned",pickup_ready_at=now) for i in range(n)]
def newo(): return OrderSim("NEW",PC,DC,None,"assigned",pickup_ready_at=now)

_real=C.load_flags
def run(flag_on, tier, bag_len):
    C.load_flags=lambda: ({**_real(), "ENABLE_HARD_TIER_BAG_CAP": flag_on})
    v,r,m,_=F.check_feasibility_v2(PC,bag(bag_len),newo(),now=now,courier_tier=tier,
                                   shift_end=datetime(2026,6,18,20,0,tzinfo=timezone.utc))
    C.load_flags=_real
    capped = v=="NO" and "hard_tier_bag_cap" in r
    return capped, m.get("would_hard_cap"), m.get("hard_tier_bag_cap")

print("flaga | tier | bag_after | would_cap | rejected_by_cap | expected")
# expected = czy ODRZUCONY przez cap (bag_after > cap_tieru). Reguła: gold/std+ max6, std max5, slow/new max4.
cases=[(True,"gold",6,False),(True,"gold",7,True),(True,"std+",6,False),(True,"std+",7,True),
       (True,"std",5,False),(True,"std",6,True),(True,"slow",4,False),(True,"slow",5,True),
       (True,"new",4,False),(True,"new",5,True),(True,"new",3,False),(False,"new",7,False)]
ok=0
for flag,tier,blen,exp in cases:
    # blen = docelowy bag_after (len(bag)=blen-1)
    capped,would,cap=run(flag,tier,blen-1)
    res = capped==exp
    ok+=res
    print(f"  {str(flag):5s} | {tier:5s} | {blen:2d} (cap={cap}) | would={would} | capped={capped} | exp={exp} {'OK' if res else 'FAIL!!'}")
print(f"\n{ok}/{len(cases)} {'PASS' if ok==len(cases) else 'FAIL'}")
print("metryka would_hard_cap liczona ZAWSZE (shadow even przy OFF):", run(False,"new",6)[1])
