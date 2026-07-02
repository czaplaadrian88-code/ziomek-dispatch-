# L09 — SIŁA STRAŻNIKÓW (AUDYT 2.0, PAS 0.H)

**Data:** 2026-07-02 · **Tryb:** READ-ONLY (zero edycji plików produkcyjnych; mutacje w pamięci)
**Pytanie pasa:** ZIOMEK_INVARIANTS.md deklaruje ~19 testów-strażników (głównie 🟢 TEST), a alokacja/feasibility = 🔴 SLOT-y (1 strażnik/5 slotów). **Czy te zielone testy cokolwiek łapią?**

**Werdykt jednozdaniowy:** Zielone testy NIE są głównie teatrem — klaster DANE/SENTINELE/STAN/CYKL-ŻYCIA to solidne strażniki BEHAWIORALNE (import produkcji + kierunkowość + kill-switch). ALE feasibility/alokacja jest strzeżona CIENKO (1-2 testy/bramkę) i ma realne dziury: **jedna twarda bramka (bag sanity-cap) nie ma ŻADNEGO testu behawioralnego** (tylko kruchy string-match z mylącą nazwą), a **strażnik verdict-gate sprawdza OBECNOŚĆ tokenu, nie POLARYTET** (inwersja `not` przechodzi niezauważona).

---

## Metoda (dowód, nie deklaracja)

Mutation-testing wykonany **bez dotykania produkcji**: wtyczka pytest (`scratchpad/mutplugin.py`) wstrzykuje ZMUTOWANĄ kopię `feasibility_v2.py` do `sys.modules['dispatch_v2.feasibility_v2']` w `pytest_configure` (przed kolekcją). Plik na dysku NIETKNIĘTY. Rodzina testów dotykających feasibility (15 plików, **baseline: 103 passed / 0 failed**). Pełna rodzina strażników/inwariantów (23 pliki): **162 passed / 5 xfailed** — czyli deklaracje „🟢 TEST" w INVARIANTS są PRAWDZIWE (testy istnieją i przechodzą), a 5 SLOT-ów jest poprawnie oznaczone `xfail(strict)` (nazwany dług, nie ciche zielone).

---

## (a) Inwentarz strażników — behawioralne vs strukturalne

**23 pliki guard/invariant.** Klasyfikacja siły (grep `inspect.getsource|read_text|ast.parse|in src|in full`):

| Klasa | Strażniki | Siła |
|---|---|---|
| **Behawioralne** (import funkcji produkcji, kierunkowość, kill-switch) | `test_coord_poison_guard`, `test_zombie01_pickup_guard`, `test_tier01_inactive_courier_guard`, `test_delivered_sink_guard`, `test_ground_truth_reconcile_guards`, `test_state_write_guard`, `test_payload_fallback_guards`, `test_fail05/09`, `test_parse_continuity_guard`, `test_bootstrap_preserve_guard`, `test_bug2_bootstrap_guard`, `test_v327_mult_sign_guard`, `test_v328_heuristic_shift_guard`, `test_canon_order_invariants`, `test_pickup_floor_guard`, `test_coord_sentinel_ingest_l21`, `test_carried_first_guard` | MOCNE (18) |
| **Strukturalne / source-inspection** (pinują TEKST, nie zachowanie) | `test_scale01_caps_flags` (9 trafień), `test_verdict_gate_guards` (3), `test_invariant_slots_l04` SLOT-5 (ast) | KRUCHE / teatr-podatne |
| **Parytet deploymentu** | `test_carried_first_guard_env_parity` (drop-iny systemd) | MOCNE (patrz niżej) |

Reprezentatywne dowody, że behawioralne są prawdziwe:
- `test_zombie01_pickup_guard.py:48` → `cr._bag_not_stale(o, NOW) is False` dla assigned+stary `picked_up_at` (kierunkowe + regres-guard + kill-switch l.72).
- `test_coord_poison_guard.py:47-64` → realne `osrm_client.route/table` + `_bag_dict_to_ordersim` — 4 warstwy sentineli.
- `test_delivered_sink_guard_2026_06_13.py:81` → napędza PRAWDZIWY `state_machine.update_from_event` na tmp-state; łapie regres None-timestamp i wyzerowania coords.

## (b) Pokrycie inwariantów — co ma realny test, co to SLOT

- **Klaster DANE/SENTINELE/STAN/CYKL-ŻYCIA (INVARIANTS §65-67, §52-56):** gęsto obstawiony BEHAWIORALNIE. To NIE teatr. ✅
- **Feasibility twarde bramki `return ("NO",…)`** — 5 miejsc: `feasibility_v2.py:459` (bag_full), `:469` (hard_tier_bag_cap, flaga OFF), `:656` (pickup_too_far), `:726` (v325_NO_ACTIVE_SHIFT), `:785` (shift_ending), `:1208` (sla_violation), `:1216` (R6 per-order), `:1269` (v324a dropoff-after-shift). Pokrycie: **cienkie** (patrz mutacje).
- **Inwarianty wyższego rzędu (twin-parity, one-source, hard-before-soft re-assert, R-declared tripwire):** **prawdziwe 🔴 SLOT** — dziś tylko `xfail`-ratchet w `test_invariant_slots_l04.py` (5 slotów). To NAZWANY DŁUG, nie egzekwowanie. Ratchet zmusza do zdjęcia `xfail` przy naprawie (XPASS→FAIL) — dobra inżynieria, ale ZERO ochrony przed regresją dziś.
- **INV-FEAS-PICKUP-FLOOR:** INVARIANTS l.29 mówi „grep dziś = 0 strażników" — **NIEAKTUALNE**: od dziś istnieje `tools/pickup_floor_guard.py` + `test_pickup_floor_guard.py` (18 testów). ALE to DETEKTOR read-only (loguje naruszenia do jsonl), NIE tripwire u źródła feasibility → inwariant nadal bez EGZEKWOWANIA, tylko obserwacja.

## (c) DEMO mutation-testingu (feasibility_v2, in-memory)

| Mutacja | Cel (plik:linia) | Wynik | Werdykt |
|---|---|---|---|
| `pickup_invert` `>`→`<` | :655 pickup_too_far | **15 failed** | ZABITA (mocno) |
| `sla_35_to_99` | :38 C2_PER_ORDER_THRESHOLD_MIN | **4 failed** (test_feasibility_c2) | ZABITA |
| `r6_disable` `if r6…`→`if False and r6…` | :1216 R6 hard-reject | **1-2 failed** | ZABITA (CIENKO — 1 test behawioralny: `test_feasibility_c3::test_r6_hard_reject_over_35_unchanged`) |
| `default_sla_999` 35→999 | :53 DEFAULT_SLA_MINUTES | **2 failed** (tylko szeroka rodzina, 0 w rdzeniu) | ZABITA (cienko) |
| `bagfull_offbyone` `>=`→`>` | :458 bag_full | **0 failed behawioralnie** | **PRZEŻYŁA** (patrz P2) |
| `reach_999` 15.0→999.0 | :51 MAX_PICKUP_REACH_KM | 0 failed | **NIEWAŻNA** (no-op — martwa stała, patrz INFO) |

**Mutation-score (ważne mutacje) ≈ 4/5 zabite behawioralnie (~80%), ale kills CIENKIE.** Hipoteza „zielone testy nic nie łapią" = FAŁSZYWA dla podstawowych bramek; PRAWDZIWA dla: (1) off-by-one bag-cap, (2) polarytetu verdict-gate, (3) inwariantów wyższego rzędu.

---

## Strażniki-teatr (zidentyfikowane)

1. **`test_feasibility_bag_filter_honors_override` (test_scale01_caps_flags.py:111)** — nazwa OBIECUJE weryfikację behawioralną filtra bag-cap („bag o rozmiarze 8 … NIE przejść przy 8"), a ciało robi TYLKO `assert "len(bag) >= _bag_cap" in full` (l.120, `inspect.getsource` = czyta DYSK). Pinuje TEKST, nie zachowanie: (a) refaktor zachowujący semantykę (`> _bag_cap-1`) fałszywie alarmuje; (b) żadnego wywołania `check_feasibility_v2` z workiem = zero weryfikacji runtime. Mutacja `>=`→`>` przeżyła wszystkie testy behawioralne; łapie ją tylko ten string-match (na dysku).
2. **`test_verdict_gate_guards.py:76`** — `guarded = any("_always_propose_on()" in c …)` wykrywa OBECNOŚĆ tokenu, NIE polarytet. Mutacja `if not _always_propose_on()`→`if _always_propose_on()` na dowolnej bramce KOORD zostawia token → `guarded=True` → test dalej zielony. Inwersja bramki quality↔operational przechodzi niezauważona. Dodatkowo regex-parsing źródła (l.52-78) pęka przy refaktorze (kruchy).
3. **`test_carried_first_guard.py`** — waliduje logikę klasyfikatora WSTRZYKUJĄC poprawny `anchor=POS` i kanon (monkeypatch `_start_anchor`/`_apply_canon_order_invariants`). Strukturalnie NIE MOŻE złapać trybu awarii, który uczynił żywy przyrząd VOID (pusty env → 91,7% fikcyjnych `no_position`). Dawał fałszywą zieleń przez tygodnie. *(De-void naprawiony osobno — patrz P4/pozytyw.)*
4. **`test_v327_mult_sign_guard.py:134` `_pipeline_mult`** — „Replika logiki bloku v327 w dispatch_pipeline" — testuje KOPIĘ kompozycji, nie realny call-site. Dryf pipeline↔replika niewidoczny. (Rdzeń `apply_bundle_score_mult` / `min_drop_proximity_factor_split` = prawdziwy, OK.)

## Pozytywy potwierdzone (nie ruszać jako problem)

- **carried_first VOID → DE-VOID DEPLOYED DZIŚ:** drop-in `/etc/systemd/system/dispatch-carried-first-guard.service.d/engine-env-parity.conf` istnieje (mtime 22:31), a `test_carried_first_guard_env_parity` egzekwuje LUSTRO env. Zweryfikowane: **14 flag ENABLE_ identyczne** silnik↔strażnik. Genuine fix, nie kosmetyka.
- Deklarowany `test_overage_cap_equals_engine_dial` ISTNIEJE (`test_bundle_calib_shadow.py:249`) — INVARIANTS l.28 prawdziwe.
- Mutacje `pickup_invert`/`sla_35_to_99` zabite mocno → kierunek bramek pickup i C2 dobrze strzeżony.

---

## Rekomendacje (priorytet)

1. **(P2)** Dodać BEHAWIORALNY test bag sanity-cap: wywołać `check_feasibility_v2` z workiem == cap → oczekiwać `("NO", "bag_full…")`, oraz cap+override. Zastąpić string-match z l.120 (albo zostawić jako uzupełnienie, nie jako jedyny strażnik).
2. **(P2)** `test_verdict_gate_guards`: wykrywać POLARYTET (`not _always_propose_on()` vs `_always_propose_on()`), nie samą obecność tokenu; docelowo test behawioralny na realnym `assess_order` pod ALWAYS-PROPOSE ON/OFF.
3. **(P2)** Dogęścić cienkie kills: R6 hard-reject (`:1216`) i DEFAULT_SLA — po ≥2 niezależne testy behawioralne, by usunięcie jednego nie odsłaniało całej klasy.
4. **(P3)** Przemianować/naprawić `test_feasibility_bag_filter_honors_override` (nazwa kłamie o zakresie).
5. **(INFO)** Usunąć martwą stałą `feasibility_v2.py:51 MAX_PICKUP_REACH_KM` (realny czytelnik = `C.MAX_PICKUP_REACH_KM`, l.105).
6. **(INFO)** Zaktualizować INVARIANTS l.29 (INV-FEAS-PICKUP-FLOOR ma dziś detektor+test; rozróżnić „detektor/monitor" vs „tripwire u źródła").

## Materialność
Nie policzalna w zł/dzień — to ryzyko REGRESJI na przyszłym refaktorze/flipie (mina), nie żywy koszt. Ekspozycja skoncentrowana tam, gdzie audyt spójności lokalizuje nawroty (feasibility/alokacja): cienkie/teatr-podatne strażniki nie zatrzymają regresji, którą wprowadzi kolejna fala L0-L8.

## Artefakty (read-only, w scratchpad — nie produkcja)
`scratchpad/mutplugin.py` (wtyczka mutacyjna). Baseline 103/0; guard-family 162 passed/5 xfailed. Mutacje odtwarzalne: `ZIOMEK_MUT=<name> pytest <rodzina> -p mutplugin`.
