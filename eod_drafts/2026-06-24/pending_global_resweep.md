# pending_global_resweep — globalny re-ranking wiszących propozycji (SHADOW)

**Data:** 2026-06-24 · **Status:** SHADOW LIVE (timer 1 min) · **Flip LIVE:** za ACK po przeglądzie 26.06

## Problem (case 483138 Chinatown→Plażowa, 24.06)

Adrian ręcznie przypisał Piotra (531), bo Ziomek zaproponował Patryka (75) i nie poprawił. Dwie luki:

1. **Brak re-rankingu wiszących.** `shadow_dispatcher` konsumuje TYLKO zdarzenie `NEW_ORDER`
   (`shadow_dispatcher.py:1071,1310`) → propozycja liczona JEDNORAZOWO przy utworzeniu
   zlecenia, nigdy ponownie. Order 483138: oceniony 1× o 19:15:01, ręcznie przypisany
   19:22:16. W międzyczasie Patryk obładował się 4. zleceniem (483141 → r6 33→42), Piotr
   się zwolnił — propozycja stała się nieaktualna, ale nikt jej nie odświeżył.
   (`postpone_sweeper` re-emituje tylko POSTPONED, bez świeżego `assess_order`.)

2. **Greedy per-order, brak globalu.** Każde zlecenie oceniane niezależnie. Gdy wisi kilka,
   ten sam „najlepszy" kurier (stojący pod restauracją) bywa proponowany do wszystkich,
   choć część jedzie w inne strony i powinna trafić do różnych kurierów.

## Rozwiązanie

`tools/pending_global_resweep.py` — co 1 min bierze WSZYSTKIE wiszące zlecenia
(w `pending_proposals.json` ∧ `orders_state.status == "planned"`) i alokuje je GLOBALNIE.

**Algorytm — sekwencyjny greedy z wirtualną alokacją (`global_allocate`):**
1. Oceń każde niealokowane zlecenie prawdziwym `DP.assess_order` nad `dispatchable_fleet`.
2. Przypisz to o najwyższym best-score → jego najlepszemu kurierowi.
3. **Dokłej** zlecenie do worka tego kuriera (`_tentative_assign` → `cs.bag += order`).
4. Re-oceń tylko zlecenia, których best był TYM kurierem (reszta niezmieniona — zmienił się
   stan jednego kuriera). Powtarzaj aż pusto.

Efekt: po wzięciu jednego zlecenia kurier jest „obciążony" → kolejne zlecenia w przeciwną
stronę dostają u niego gorszy score → **rozjeżdżają się na różnych kurierów**.

**Porównanie z propozycją** robione po AKTUALNYM score proponowanego kuriera
(`cand_scores` z chwili alokacji, NIE po score sprzed obładowania — klucz do single-rerank).
Reasony: `rozjazd_kierunkow` / `lepszy_kurier` / `proponowany_wypadl` / `bez_zmian` /
`brak_feasible_kuriera_KOORD`.

## Bezpieczeństwo

- READ-ONLY, OSOBNY proces, ZERO mutacji panel/Telegram/state (jak `reassignment_forward_shadow`).
- `_disable_replay_capture()` wołany w `global_allocate` (NIE na imporcie — żeby import do
  żywego dispatchu nie ubił `obj_replay_capture`). Syntetyczne assess z wirtualnymi workami
  NIE mogą skazić zestawu kalibracyjnego.
- Bezpiecznik `MAX_HANGING=8` (O(K²) assess, ale re-oceniane tylko zlecenia z best=obciążony).

## Flagi (flags.json — runtime, nie w repo)

| flaga | default | rola |
|---|---|---|
| `ENABLE_PENDING_RESWEEP` | False | master shadow on/off (LIVE: True) |
| `PENDING_RESWEEP_LIVE` | False | faktyczne re-proponowanie (edit msg) — NIEWPIĘTE |
| `PENDING_RESWEEP_MARGIN` | 15.0 | próg pkt „istotnie lepszy" |

Rollback hot = `ENABLE_PENDING_RESWEEP=false`. Backup: `flags.json.bak-pre-resweep-2026-06-24`.

## Deploy

- `systemd/dispatch-pending-resweep-shadow.{service,timer}` — `OnUnitActiveSec=1min`,
  sandbox (Mem 400M / OOMScoreAdjust 200 / Nice 10) jak inne shadow-serwisy.
- `tools/pending_global_resweep_review.py` + `systemd/dispatch-pending-resweep-review.{service,timer}`
  — one-shot `OnCalendar=2026-06-26 07:00 UTC` (=09:00 Warsaw, po peaku 25.06): raport GO/NO-GO
  + Telegram do Adriana (`send_admin_alert`).
- Testy `tests/test_pending_global_resweep.py` 8/8. Loguje `dispatch_state/pending_global_resweep.jsonl`.

## Shadow → LIVE (za ACK po 26.06)

Jeśli korpus pokaże sensowną liczbę would_repropose / rozjazdów pile-on (rzeczy robione dziś
ręcznie) → wpiąć ścieżkę LIVE za `PENDING_RESWEEP_LIVE`: **edit istniejącej wiadomości TG po
`message_id`** (bez spamu — update w miejscu) + atomowy update `pending_proposals.json` z
**lockiem** względem żywego `telegram_approver` (współdzielony plik). Tej ścieżki świadomie nie
wpięto teraz — wymaga ostrożnego lockowania, nie na szybko.
