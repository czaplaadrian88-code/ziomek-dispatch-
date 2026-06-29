# OUTCOME-JOIN objm-lexr6 D2 — brama §6 (2026-06-24, read-only)

Join flipów `best.objm_lexr6_*` (shadow_decisions) ↔ `backfill_decisions_outcomes_v1.jsonl` po `order_id`.
Cel: brama outcome — czy `+new-late` (D2 opóźnia nowe zlecenie) psuje R6 NOWYCH zleceń downstream.
Narzędzie: `scratchpad/objm_lexr6_outcome_join.py`.

## Pokrycie
- Flipy: **174** (170 unikalnych order_id), **174/174 w backfill, wszystkie delivered** — 100% śledzalne (15–23.06).

## ⚠️ Konfundent: realny tor ≠ pick Ziomka (override 79%)
Na decyzjach flipowych koordynator nadpisał Ziomka w **138/174 (79%)**. Realny wykonawca:
| Kto faktycznie dowiózł | n |
|---|---|
| LIVE-pick Ziomka (wykonany) | 36 (21%) |
| **D2-pick (carry-aware) — override trafił dokładnie w D2** | **25** ✅ |
| Ktoś trzeci (koord wybrał innego) | 113 |

→ D2 nigdy nie wykonany (shadow); w 79% nawet live-pick nie wykonany. **Outcome-join nie może bezpośrednio
potwierdzić symulacji D2** — może wykluczyć szkodę + dać korroborację.

## Realne wyniki (R6 = pickup→delivery, próg 35 min)
- Flipowe zlecenia: **breach 8,0% (14/174)** vs **baseline cały backfill 10,5% (183/1739)** → flipy ≤ baseline (nie gorzej).
- Czysty subset (36 gdzie live-pick wykonany): **breach 8,3%, med R6 15,4 min**.
- **25 flipów: człowiek niezależnie nadpisał na DOKŁADNIE D2-pick** → realna (skromna) korroboracja carry-aware wyboru.

## Brama downstream-harm („+new-late psuje R6 nowych")
- `d_new_late > 0` (D2 opóźnia nowe): **33 flipy** (med +4,4 min, max +35,1).
- Konserwatywne górne oszacowanie (real_R6 + całe opóźnienie odbioru → R6): **5 potencjalnych nowych breachy**.
- ALE wszystkie 5 (i 24/33 subsetu) to **override** → liczone na torze, którego nikt nie wykonał; projekcja zawyża
  (opóźnienie odbioru bije głównie w punktualność committed, nie w pickup→delivery). Agregat estym. `d_new_late = −70 min` (D2 na plus).

## WERDYKT bramy outcome: 🟢 NIE wykazała szkody → nie blokuje Fazy 2 (potwierdzenie słabe, skonfundowane)
- ✅ Zero realnych dowodów szkody downstream (flipy ≤ baseline; harm-bound ≤5, wszystkie kontrfaktyczne/override).
- ✅ Korroboracja: 25 override → D2-pick.
- ⚠️ Realny kanał zaślepiony 79% override; D2 nieobserwowalny → kontrfakt niedostępny.

**Rekomendacja:** bramki §6 (−533 min / 0,56% / n=1432) trzymają na symulacji; outcome-join nic dyskwalifikującego.
Faza 2 uzasadniona ZA ACK Adriana — ale ponieważ realna walidacja wyszła pośrednia, live-flip puścić jako
**canary z trackingiem override**, nie od razu 100%. 79% override na flipach = osobny sygnał (sporne decyzje, koord wchodzi masowo).
