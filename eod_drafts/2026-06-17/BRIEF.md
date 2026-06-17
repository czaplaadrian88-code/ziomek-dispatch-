# BRIEF — Audyt kontrfaktyczny Ziomka (trudne decyzje, 7d 2026-06-10..16). READ-ONLY.

Jesteś agentem śledczym w audycie dispatchera „Ziomek" (Białystok). **TRYB READ-ONLY.** Nie edytuj
kodu prod, nie flipuj flag, nie restartuj usług. OSRM read-only OK. Produkt = ustalenia + dowody.

## Pytanie centralne
Ziomek przelicza wszystkie dane, więc na trudnych decyzjach powinien być LEPSZY od człowieka — a bywa
odwrotnie. Szukamy decyzji gdzie Ziomek wybrał kuriera, choć w JEGO WŁASNEJ zalogowanej puli istniał
kurier **ściśle lepszy** (Pareto) na prawdziwym celu (R6 ≤35, committed-odbiór, spóźnienie nowego
odbioru). Dla każdej: bug, miskalibracja score, czy uzasadniony trade-off?

## Co już ustalone deterministycznie (NIE podważaj bez dowodu — to twój punkt startu)
Skrypt `analyze.py` przeszedł 1712 decyzji w oknie i policzył **DOMINACJĘ**: kandydat A z puli ściśle
dominuje wybrany `best` B, gdy A jest ≤ na każdej osi (r6_max_bag, late_pickup_committed_max,
new_pickup_late, objm_r6_breach_count), ≥ na zaufaniu pozycji (pos_source) i feasibility, i ostro
lepszy na ≥1 osi. Wynik:
- **165/1712 (9.6%) decyzji ma `best` ściśle zdominowany** przez zalogowaną alternatywę.
- Po odfiltrowaniu artefaktów (r6>300 = zombie pickup) i KOORD (człowiek przejął): **118 realnych
  propozycji**. Z tego: 10 „committed-breach unikalny" (dCommit>2min, Σ70min), 22 „R6-unikalny >10min",
  reszta minor.
- **E2-signature (order_id%5==0) = 28/118** — ledwo ponad bazową 20%, więc problem JEST szerszy niż
  znany bug E2-pln.
- Mechanizm: **111/118 (90%) dominator miał NIŻSZY score Ziomka** → score systematycznie nie-docenia
  prawdziwego celu. **40/118 (34%) `best` BYŁ score-topem** (czysta miskalibracja); 78 = override
  zdemotował lepszego.
- **cid=123 (Bartek) = top-dominator 31× (23 z nich is_coordinator=true)** — to GPS-owy, realny kurier
  (184 dostawy), ale Ziomek demotuje IDLE koordynatora o **−100** (`bonus_coordinator_idle`,
  dispatch_pipeline.py:3861-3865). Więc 123-dominacje to PRAWDOPODOBNIE by-design rezerwacja
  koordynatora — ZWERYFIKUJ per-case (czy 123 był naprawdę wolny / czy −100 nie jest zbyt ostre przy
  dCommit≈20min).

## Znany bug WYKLUCZONY (nie raportuj jako nowy; potwierdź tylko że fix pokrywa)
**E2-pln pure-resort**: eksperyment `ENABLE_E2_PLN_AB` (ON całe okno) dla order_id%5==0 re-sortował
kandydatów po `pln_v` (płaca), kasując demote tier2 → Ziomek wybierał łamiącego committed (np. 179)
zamiast nie-łamiącego (123/484) choć ten miał WYŻSZY score. Root: `dispatch_pipeline.py:_pln_pure_resort`
(l.863). **Fix B+C LIVE od 17.06 ~01:35** (`ENABLE_PLN_RESORT_WITHIN_TIER` + `ENABLE_PLN_QUALITY_AWARE`):
nowy sort `(tier2-na-koniec, bucket, pln)` → łamiący committed NIGDY nie bije nie-łamiącego. Okno 7d jest
SPRZED fixa, więc E2-sig cases (np. 481340, 481080) to ten bug. Potwierdź że fix B by je pokrył; klasyfikuj
jako `E2_fixed`.

Inne wykluczone (naprawione 14-16.06, potwierdź tylko brak regresji): KOORD-przy-saturacji (always-propose
ON), R1-fantomowy-odbiór, pos_source=None→GPS (kosmetyk).

## Reguły biznesowe (arbiter „lepszej opcji")
- **R-35MIN-MAX (HARD):** dostawa ≤35 min od gotowości (R6). `r6_max_bag_time_min` = najgorsza dostawa w
  worku PO wzięciu nowego; >35 = breach (`objm_r6_breach_max_min` = ile ponad).
- **R-DECLARED-TIME (HARD):** nie łam umówionego (committed) odbioru cudzego zlecenia.
  `late_pickup_committed_breach`/`_max` = czy/ile łamie.
- **R-NO-WASTE / R-FLEET-LEVEL:** optymalizuj flotę, nie 1 order. „Najbliższy wolny", który łamie czyjś
  commit lub psuje cudzy worek = NIE jest lepszy.
- **score ≠ wynik** (bartek2): niski score sam w sobie nie znaczy zła decyzja; arbiter = kontrfaktyka +
  rzeczywisty outcome.
- **Decision-time vs hindsight:** „lepsza opcja" liczona z danych DOSTĘPNYCH w chwili decyzji (zalogowana
  geometria Ziomka). `outcome` (rzeczywista dostawa) = osobno, etykieta „hindsight".

## Ograniczenie danych (ważne)
`obj_replay_capture.jsonl` = 1 wiersz/decyzja (TYLKO wybrany kurier — jego pozycja+worek). Pełna pula =
`shadow_decisions.alternatives[]` (do 15 feasible kandydatów, każdy z pełnym rozbiciem celu, ALE bez
surowej pozycji lat/lng — tylko `drive_min`/`km_to_pickup` policzone przez Ziomka z OSRM w chwili decyzji).
Więc: porównanie best-vs-alt robisz na ZALOGOWANEJ geometrii Ziomka (ta sama maszyna dla obu = uczciwe,
konserwatywne). OSRM niezależnie sprawdza TYLKO wybranego (z capture) + outcome. Pozycji alternatyw nie
odtworzysz niezależnie — to udokumentowane ograniczenie, nie zgaduj.

## Narzędzia (uruchamiaj z `/root/.openclaw/workspace/scripts`)
- PY=`/root/.openclaw/venvs/dispatch/bin/python`
- Per-case: `$PY dispatch_v2/eod_drafts/2026-06-17/casetool.py <order_id> [<order_id>...]`
  → drukuje: CHOSEN BEST + każdy DOMINATOR (OBJ osie / DISQ ukryte dyskwalifikatory / SCORE rozbicie),
  geometrię capture + niezależny OSRM wybranego, i OUTCOME (rzeczywista dostawa). To twoje główne źródło.
- Artefakty (JSON): `deepdive_cases.json` (52 case'y), `dominated_cases.json` (165), `slim_shadow_index.json`.
- Pełny rekord: `grep '"order_id": "<oid>"' /root/.openclaw/workspace/scripts/logs/shadow_decisions.jsonl`
  (każda linia = 1 decyzja, ~59KB; sprawdź top-level order_id).
- Kod selekcji: `dispatch_pipeline.py` (_pln_pure_resort l.863; coordinator −100 l.3861; bonus_penalty_sum
  l.4228; E2 hook l.5206; KOORD gate l.5347), `wave_scoring.py`, `feasibility_v2.py`, `common.py`.

## DISQ — ukryte dyskwalifikatory dominatora (sprawdź ZANIM uznasz „lepszy")
Dominator może być słusznie zdemotowany przez coś poza 4 osiami: `is_coordinator`+`bonus_coordinator_idle`
(−100), `new_courier_ramp`/`v325_new_courier_*`, `v326_wave_veto`, `*_hard_reject` (carry_chain/
intra_rest_gap/v3273_wait_courier), `fifo_violations`, `shift_end_edge`, `paczka_*`. casetool drukuje je w
DISQ. Jeśli dominator ma realny dyskwalifikator → to NIE czysty bug, lecz uzasadniony wybór (klasyfikuj
`hidden_disqualifier`).

## Taksonomia klasyfikacji (użyj DOKŁADNIE tych etykiet)
- `E2_fixed` — order_id%5==0 + dominator miał wyższy score + best łamie committed → bug E2-pln, fix B pokrywa.
- `coordinator_reservation` — dominator=koordynator (is_coordinator) demotowany −100; by-design. Pod-flaga
  `policy_review` jeśli dCommit>10 lub dR6>20 (warte decyzji Adriana czy −100 zbyt ostre).
- `new_courier_ramp` — dominator pod rampą nowego kuriera; by-design.
- `hidden_disqualifier` — dominator ma wave_veto / hard_reject / fifo / shift_end → wybór best uzasadniony.
- `score_miscalibration_REAL` — brak ukrytego powodu; dominator realnie lepszy, a score Ziomka go nie-docenił
  (lub override go zdemotował). TO są potencjalne nowe bugi. Podaj OŚ score winną (która `bonus_*` przeważyła).
- `saturation_leastbad` — pool_feas==0, wszyscy infeasible; sprawdź czy best to naprawdę najmniej-zły.
- `data_artifact` — r6>300 / zombie / zła geometria.
- `minor_noise` — dominacja <~3min na 1 osi, bez realnego wpływu (outcome dostarczony normalnie).

## Twardo
Każda teza = order_id + liczba + plik:linia. Replay/pomiar. Konserwatywnie: domyślnie „decyzja
uzasadniona", chyba że DOWIEDZIONO że istniała ściśle lepsza FEASIBLE i ASSIGNOWALNA opcja. Warsaw TZ (UTC+2).
