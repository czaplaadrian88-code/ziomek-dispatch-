# HANDOFF → sesja tmux 211 (ziomek-full-rule-audit)

**Od:** równoległa sesja CC (Adrian poprosił o przekazanie). **Data:** 2026-06-24.
**Status: NIC nie flipnięte, prod nietknięty, wszystko read-only.** To gotowa, zwalidowana
diagnoza JEDNEJ konkretnej niespójności precedencji — wepnij do swojej tabeli HARD-vs-SOFT
(powierzchnia: `plan_recheck` kanon+relax). Powiązana pamięć: `memory/carried-first-vs-committed-window-2026-06-24.md`.

---

## TL;DR (pasuje wprost do Twojego audytu precedencji)

**HARD reguła „okno odbioru ±5" (R-DECLARED-TIME / R27) PRZEGRYWA z SOFT preferencją „carried-first"
w ścieżce `plan_recheck` (ta, która pisze zapisany plan i konsolę koordynatora).** Zamierzona
precedencja: committed pickup window = HARD, carried-first = SOFT. Faktyczna: odwrotnie. ❌

## Case źródłowy (Michał K cid=393, 2026-06-24 ~15:33)

- Worek: **483028** (rest. Węglowa 1 „Piwo Kaczka Sushi" → **Wiejska 8/60**, odebrane 15:24, niesione)
  + **482993** (rest. Akademicka 30 „Pawilon Towarzyski" → **Upalna 11/18**, committed `czas_kuriera`=15:32).
- Ziomek (carried-first): dowieź Wiejską NAJPIERW, potem odbierz Pawilon → odbiór **15:51 vs 15:32 = +19 min**
  (shadow `pickup_lateness_shadow.jsonl` zalogował +24.7 min @13:38). Łamie ±5.
- Adrian: „najpierw kaczka-pawilon, potem wiejska-upalna" = odbierz oba, potem dowieź. Geometrycznie −8 min jazdy.

## Root cause — 3 warstwy z niespójnym celem (`plan_recheck._gen_one_bag_plan`)

1. `:599` tie-breaker committed (`ENABLE_PLAN_RECHECK_COMMITTED_PROPAGATION=1`) — propaguje `czas_kuriera`
   do sim → kara N5 chciałaby odbiór na czas. ✅ ale…
2. `:661` `_apply_canon_order_invariants` — **BEZWARUNKOWO wpycha niesione (picked_up) dostawy na PRZÓD**
   → KASUJE wynik warstwy 1 dla każdego worka z jedzeniem w aucie → odbiór znów late. ❌ to jest override.
3. relax `_relax_carried_first` (`:842`) — jedyna furtka z carried-first, ale: (a) committed użyte TYLKO
   jako podłoga „nie odbieraj za wcześnie" (`:936`), (b) spóźnienie odbioru liczone WZGLĘDEM carried-first
   baseline (`:994`), NIE względem bezwzględnego okna `ck+tol`, (c) twardy cap świeżości carried
   `CARRIED_FIRST_RELAX_SOFT_MAX_MIN=20` (TOL=3, EPS=0.3 — zweryfikowane bezpośrednio z modułu pod prod-env;
   uwaga: agenci Explore mylili te wartości i numery linii — NIE ufaj im, czytaj kod). Relax odrzucił trasę
   Adriana o **0,2 min** (carried Wiejska 20,2 > cap 20,0) — choć R6=35 daje 15 min zapasu.

**Systemowe, nie 1 case:** `pickup_lateness_shadow.jsonl` = **110 zleceń/dzień** >±5 (rekord cid393 oid482813
„Sushi Rany Julek" +103,7 min). Detekcja działa, egzekucji/spójności brak.

## ⚠ LOAD-TIERING (Adrian to wychwycił — NIE pomiń, NIE duplikuj istniejącej pracy)

Tolerancja okna odbioru jest **load-aware**, NIE płaskie ±5:
- `OBJ_COMMITTED_PICKUP_TOL_STRICT_MIN=5` / `LOOSE_MIN=10`, przełącznik `loadgov_ewma ≥ OBJ_COMMITTED_PICKUP_LOAD_THRESHOLD=4.5`.
- Ustawiana przez `dispatch_pipeline:3062` → `route_simulator.set_committed_pickup_tol`; kara soft coeff **100 / T2 400**
  + eskalacja (`ENABLE_OBJ_COMMITTED_PICKUP_PENALTY` flags.json=True, `ENABLE_OBJ_COMMITTED_PICKUP_ESCALATION`).
- **Ta maszyneria JUŻ ISTNIEJE — ale TYLKO w ścieżce MAIN (assess_order / route_simulator).** W peaku spóźniony
  odbiór jest CZĘŚCIOWO ZAMIERZONY (ściana pojemności). Nie duplikować.

**Dwie luki (wąski realny problem):**
1. `plan_recheck` NIGDY nie woła `set_committed_pickup_tol` (osobny proces; grep loadgov/tol w plan_recheck = 0)
   → jego sim w `_gen_one_bag_plan` używa **strict 5 ZAWSZE**, niespójnie z main w peaku.
2. Kanon `:661` + relax flat-20 load-blind KASUJĄ karę committed dla worków z niesionym.

## Replay offline (zwalidowane, read-only) — werdykt GO, ZERO-HARM

Narzędzie: `eod_drafts/2026-06-24/lex_window_replay.py` (reużywa prymitywów `eod_drafts/2026-06-22/carried_first_replay.py`
+ korpus `dispatch_state/obj_replay_capture.jsonl` ~82k rec). Env `PICKUP_WINDOW_MIN` (5 lub 10). Dump: `dispatch_state/lex_window_changed_cases.jsonl`.

Porównanie vs LIVE (carried-first + relax@20), 2500 dedup carried-active worków:
- **C NAIWNY** (okno=top tier, bez ograniczeń) = SZKODLIWY: 255-295 R6 harm / 229-283 carry>35 / 673-709 dostaw >5min.
  → „po prostu promuj okno odbioru na szczyt" = ZŁE. Dowód, że łatanie pojedynczego progu psuje resztę.
- **D SPÓJNY (constrained lex)** = ZERO-HARM pod OBIEMA tolerancjami:
  - tol=5 (normal): **−11% naruszeń okna** (−2262 min), R6 harm=0, carry>35=0, deliv>5 harm=0, jazda netto **+3077 min**.
  - tol=10 (shortage): **−16%** (−3046 min), 0/0/0 harm, jazda netto **+3134 min**.
  - najgorszy wzrost świeżości carried +25,5 min, zawsze ≤35.
  - na ORYGINALNYM worku Michała K D wybiera DOKŁADNIE trasę Adriana (Pawilon P first → Wiejska D → …;
    +17,6→0 late, drive −8 min, carry 18,6→**20,2** = ta sama liczba, którą cap 20,0 relaxa odrzucił).

**Definicja D (constrained lex):** wśród permutacji precedence-valid + NO-RETURN, FEASIBLE = {brak nowego R6
breach vs live, carried ≤ R6(35), żadna INNA dostawa nie opóźniona >TOL vs live} → minimalizuj
`(naruszenia_okna, jazda, wiek_carried)`. Carried-first = emergentna miękka preferencja, NIE twardy niezmiennik.

## Spójny fix (reużycie, NIE nowy płaski próg) — do Twojej spec odporności

NIE wprowadzać nowego płaskiego ±5/35. Zamiast tego:
1. Podpiąć ISTNIEJĄCĄ load-aware tolerancję (5/10 z loadgov) do `plan_recheck` (woła `set_committed_pickup_tol`
   jak `dispatch_pipeline`).
2. Kanon carried-first + relax mają USTĘPOWAĆ committed-aware sekwencji: cap świeżości carried ustępuje R6=35
   gdy to naprawia naruszenie okna; okno odbioru wchodzi jako term rankingu (dziś NIEOBECNY — primary=jazda).
3. Scalić kanon+relax+tie-breaker w JEDNĄ lex-rangę: HARD (okno load-aware → R6 → non-regresja dostaw),
   SOFT (jazda → świeżość). To Twoja „tabela precedencji" w jednym miejscu zamiast 3 kłócących się warstw.

Najbliższa istniejąca praca: committed-propagation tie-breaker (22.06, `memory/committed-propagation-resequencer-2026-06-22.md`)
— gated do non-regresji dostaw, NIE pokonuje override'u kanonu. To rozszerza tamto, nie nadpisuje.
Powiązane też: `route-reorder-fix-mk-2026-06-24` (inny mechanizm: coloc-pickup + non-carried reorder, LIVE dziś).

## Bramki przed live (uzgodnione z Adrianem)
1. Full-corpus replay (~29k) — zalockować liczbę (sygnał stabilny 400→1500→2500).
2. Shadow logger D-vs-live + impl za flagą REUŻYWAJĄCA committed-tolerancji.
3. Tunable: margines świeżości (danger-zone już 32, hard 35, best-effort stretch 40).

## Reguły Adriana (trzymaj)
- Carried-first HARD chroni stygnące jedzenie — NIE usuwać; ma tylko USTĘPOWAĆ twardszym regułom (okno, R6).
- „>35 dostawa wygrywa", relaks tylko ≤20 (delay_tol=3 = optimum wg wcześniejszych replayów).
- PRZED flipem: udowodnij replayem/pomiarem zero-harm. Net-szkodliwe/no-op → nie rób.
- Per-step ACK, .bak, py_compile, atomic writes, NIE restartuj dispatch-telegram bez ACK.
