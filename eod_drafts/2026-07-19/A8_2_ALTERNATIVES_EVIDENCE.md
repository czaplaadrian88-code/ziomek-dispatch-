# A8-2 — kompletność `best + alternatives`

Data: 2026-07-19 UTC
Branch: `fix/audit-a8-alternatives`
Worktree: `/root/wt-audit-a8-pkgroot/dispatch_v2`
Base: `486bac4682e8a5e7e696ba7efc4d5acbd08d62bc`
Model tier: `sol`
Effort: `xhigh` — P1 psujący wejście werdyktu kalibracji ETA i dotykający wspólnego kontraktu ledgera.
Dokładny wariant modelu: nieatestowany/dziedziczony; bieżący interfejs sesji nie udostępnia wiarygodnego pola modelu do potwierdzenia.

**Branch = 2 commity.** Commit 1 `863d11c` (poniżej, niezmieniony — pinowany,
nie amendowany) naprawił jedyne miejsce, które audyt `05b_RUNTIME_DEEP_AUDIT`
wskazał wprost: `shadow_dispatcher.py:938`. Ta sama sesja prep (mandat CTO,
Przykazanie #0 „bliźniacze ścieżki razem") znalazła strukturalnie identyczny
wzorzec w `czasowka_scheduler.py` (6 miejsc), nieobjęty oryginalnym findingiem
i nieobecny w `twins-registry.json` — stąd commit 2, opisany w sekcji „A8-2b"
niżej, ZANIM branch idzie do świeżej ślepej recenzji. **SHA-256 pin z sekcji
„Dowody testowe" niżej dotyczy WYŁĄCZNIE commitu 1 (pierwszy blind bundle,
sprzed rozszerzenia) — nieważny dla całego brancha po commicie 2; nowy bundle
wymagany.**

## Problem i dowód

`shadow_dispatcher._serialize_result` budował `alternatives` jako
`result.candidates[1:]`. To zakładało `best is candidates[0]`, choć trzy legalne
ścieżki `core/selection.py` łamią to założenie:

- best-effort OBJM może wybrać obiekt ze środka listy bez jej przestawienia;
- `solo_fallback` tworzy nowy obiekt `best`, a w `candidates` może pozostać
  odrzucony wariant tego samego kuriera;
- `no_solo_candidates` zwraca `best=None`, więc pierwszego elementu nie wolno
  odcinać.

Skutek: ledger mógł zgubić niewybranego kuriera, zdublować wybranego i skierować
`eta_calibration_logger` do fallbacku `matched_courier=False` mimo obecności
realnego kuriera w ocenionej puli.

Read-only replay kanonicznym `tools.ledger_io.iter_shadow_decisions` za ostatnie
3 dni potwierdził materialność bez wypisywania PII: `563` rekordy, `1`
`solo_fallback`, i dokładnie ten rekord miał `best` powtórzony w
`alternatives`.

## Mapa kompletności ETAP 3

| Miejsce | Rola | Writer/consumer | Dotknięte | Powód | Test/dowód |
|---|---|---|---|---|---|
| `core/selection.py` zwykłe ścieżki `top` | kształt wyniku | writer | N-D | `best==candidates[0]`; zachowanie pozostaje bez zmian | byte-parity winner-first |
| `core/selection.py` best-effort OBJM | kształt rozbieżny | writer | N-D | fix u wspólnego źródła ledgera, bez zmiany selekcji | winner ze środka + mutation probe |
| `core/selection.py` `solo_fallback` | kształt rozbieżny | writer | N-D | należy usunąć bliźniaka po `courier_id`, nie zmieniać decyzji | test solo-twin |
| `core/selection.py` `no_solo_candidates` | kształt rozbieżny | writer | N-D | `best=None` oznacza pełną pulę alternatyw | test no-solo |
| `dispatch_pipeline.PipelineResult` | schema granicy | writer | N-D | schema bez zmian | istniejące testy PipelineResult |
| `shadow_dispatcher._serialize_result.best` | LOCATION B | writer | N-D | serializacja best bez zmian | parytet A/B + byte-parity |
| `identity.schema.canon_cid` | wspólny kanon tożsamości | consumer | TAK | deduplikacja używa istniejącej normalizacji, m.in. `200 == 200.0`; brak lokalnej kopii resolvera | test reprezentacji numerycznych |
| `identity.candidate_pool.{candidate_identity_key,alternative_candidates}` | LOCATION A / pool (od commitu 2) | writer | TAK | wyniesione z `shadow_dispatcher.py` do współdzielonego modułu (Faza A `identity/` już eksportuje `canon_cid`; sam moduł duck-typed na `.courier_id`, zero zależności od `core.candidates`/`dispatch_pipeline` → bez ryzyka cyklu import) — `shadow_dispatcher.py` i `czasowka_scheduler.py` importują TĘ SAMĄ implementację, zero duplikacji ciał | 12 testów (8 + 4 nowe) + mutation probe obu konsumentów |
| `shadow_dispatcher._serialize_candidate` | serializer alternatywy | writer | N-D | pola i schema bez zmian | serializer cluster |
| `czasowka_scheduler._eval_czasowka_impl` (6 miejsc: l.358/367/392/401/413/422) | drugi producent `best+alternatives` z tego samego `core.decide._decide()` | writer | TAK | identyczny wzorzec `candidates[1:]` jak commit 1, niewymieniony w oryginalnym findingu (`05b_RUNTIME_DEEP_AUDIT` celował wyłącznie w `shadow_dispatcher.py:938`) ani w `twins-registry.json` przed tym commitem — znaleziony przez `grep -rn 'candidates\[1:\]'` w sesji prep | 4 nowe testy (OBJM/solo_fallback/no_solo + KOORD-alert) + mutation probe |
| `czasowka_scheduler._format_koord_alert` | konsument Telegram „Top 3 odrzuconych" | consumer | TAK (pośrednio) | czyta `result.get("alternatives")` wprost (nie `all_candidates_for_proactive`); przed fixem mógł pokazać tego samego kuriera 2× (best + jego odrzucony bliźniak) lub zgubić realnie odrzuconego (index-0 zawsze odcięty) | `test_koord_alert_top3_no_loss_no_duplicate` |
| `observability.candidate_logger` hook w `czasowka_scheduler.eval_czasowka` | obserwowalność | consumer | N-D | czyta `result.get("alternatives")` wprost, defensywnie `try/except pass`; kształt naprawiony u źródła w `_eval_czasowka_impl` — brak osobnej logiki do zmiany, ćwiczony pośrednio przez `eval_czasowka()` w nowych testach | wywoływany (nie asercjonowany osobno) w 4 nowych testach czasówki |
| `czasowka_proactive/{score_selector,evaluator}` | ranking kontrfaktyczny czasówki | consumer | N-D | oba preferują `all_candidates_for_proactive` (pełna, nieciachana lista — inny klucz, bez zmian w tym commicie); `evaluator` spada na `best+alternatives` tylko gdy `CZASOWKA_PROACTIVE_USE_ALL_CANDIDATES=False`, i wtedy dostaje już naprawiony, kompletny kształt | N-D — konsument dalszej, już naprawionej listy |
| `feasibility_v2._po_drodze_waves` pętla `for o in candidates[1:]` (l.247) | grupowanie w fale czasowo-przestrzenne | writer | N-D | fałszywe trafienie grepu tej samej klasy: `candidates` to tu świeżo posortowana lista `Order` (nie decyzyjnych `Candidate`), pętla porównuje sąsiadów parami (`o` vs `last`) — brak pojęcia „best"/wykluczenia, `[0]` explicite zainicjowany jako pierwsza fala linijkę wyżej, nie odcięty | manualna inspekcja + brak zmiany zachowania |
| `.claude/skills/ziomek-cto/references/twins-registry.json` | mapa bliźniaków (ETAP 3) | dokumentacja | TAK | nowa klasa `alternatives-best-identity` (5 miejsc, w tym oba pliki testów) — bez niej `ziomek-cto scope` nie surfacował `czasowka_scheduler.py` wcale (zweryfikowane: scope PRZED zmianą trafiał tylko w `nowa-metryka`, N-D dla tego bugu) | żywy grep w driverze: `OK (×2/×3/×3)` na wszystkich 3 symboli kodu |
| `core/jsonl_appender` / `shadow_decisions.jsonl` | persystencja | writer | N-D | API zapisu bez zmian; rekord zmienia się tylko w wadliwych kształtach | full regression + replay |
| `eta_calibration_logger` | join realnego kuriera | consumer | N-D | konsumuje tę samą schemę, dostaje kompletną pulę | E2E `matched=True` |
| `telegram_approver` | propozycje/przyciski | consumer | N-D | schema bez zmian; pula staje się unikalna i kompletna | proposal/panel cluster |
| narzędzia replay/ML/statystyki | konsumenci generyczni | consumer | N-D | wszystkie czytają `best + alternatives`; brak nowego pola | pełna regresja |
| `tools/ledger_io` | rotation-aware oracle | consumer | N-D | użyty wyłącznie read-only do dowodu materialności | agregat 563/1/1 |
| `shadow-jobs-registry` / at#220 | przyszły werdykt ETA | consumer operacyjny | N-D | aktualizacja wspólnej pamięci i decyzja o jobie należą do MAIN | HOLD do deployu i świeżego okna |

Każde miejsce zwrócone przez `ziomek-cto scope` jest TAK albo N-D. Klasa
`nowa-metryka` jest N-D: nie dodano klucza ani producenta metryki. Klasa
`artefakt-werdykt` jest dotknięta pośrednio: test joinu ETA i rotation-aware
replay stanowią oracle; sam job nie został zmieniony.

## Zmiana zachowania

- `best`, verdict, reason, score, feasibility, plan i wszystkie reguły HARD/SOFT
  pozostają bajtowo niezmienione.
- Dla zwykłego `best==candidates[0]` cały rekord jest bajtowo identyczny.
- Dla rozbieżnych kształtów `alternatives` zawiera każdego niewybranego kuriera
  dokładnie raz, w oryginalnej kolejności, i nie zawiera wariantu o kanonicznym
  `courier_id == best`.
- Dla `best=None` zachowana jest cała lista kandydatów.
- Dwa różne wadliwe obiekty bez CID nie są po cichu scalane; bez tożsamości
  można bezpiecznie rozpoznać tylko ponowne wystąpienie tego samego obiektu.

## Dowody testowe

N-D: `core/candidates.py` — zmiana nie dotyka producenta Candidate ani metrics; naprawiany jest wyłącznie wybór elementów już gotowej listy na granicy serializacji.
N-D: `scoring.py` — score, kolejność rankingowa i agregacja wyniku pozostają bez zmian; filtr używa wyłącznie kanonicznego `courier_id` po zakończonej selekcji.
N-D: `czasowka_proactive/{score_selector,evaluator}` — oba wolą `all_candidates_for_proactive` (pełna lista, klucz osobny, bez zmian); wariant `best+alternatives` dostaje teraz kompletny kształt niezależnie od `CZASOWKA_PROACTIVE_USE_ALL_CANDIDATES`.
N-D: `feasibility_v2.py:247` — inna klasa `candidates[1:]` (pętla po posortowanych `Order`, nie `Candidate`; `candidates[0]` explicite zainicjowany linijkę wyżej jako pierwsza fala, nie odcięty; brak pojęcia best/wykluczenia). Zweryfikowane grep-sweepem całego repo — jedyne dodatkowe trafienie tej klasy poza czasówką.
regresja: pełna suita `pytest tests/` po obu commitach (ta sesja): 5142 passed, 9 failed, 77 skipped, 7 xfailed w 302.90s; vs przed commitem 2 (mój pomiar, ten sam venv/środowisko): 5138 passed/9 failed/77 skipped/7 xfailed — delta +4 passed (dokładnie 4 nowe testy czasówki), nowych failed: 0, nowych skipped: 0, nowych xfailed: 0; 9 failed to identyczne nodeidy obu stron, zweryfikowane linia-po-linii (conftest_flag_strip_guard×3, flag_doc_coverage×3, 3×script_run — pkgroot-harness noise, sprzed A8-2 w ogóle). Osobno, commit 1 solo wg autora (inne środowisko/uruchomienie tego samego dnia): baseline 5133 passed / post 5141 passed, failed: 0 nowych.
e2e: `_serialize_result` → rekord `best + alternatives` → `eta_calibration_logger.pick_prediction`; realny kurier z dawnego `candidates[0]` kończy z `matched=True`, a stara mutacja daje `matched=False`. Analogicznie czasówka: `eval_czasowka()` → rekord → `_format_koord_alert`; z fixem `cid=100` (realny odrzucony) nie znika i `cid=200` (bliźniak) nie dubluje się.
pozytywny-wplyw: mutation replay shadow_dispatcher `5 failed, 3 passed` na starym `[1:]` i `8 passed` po fixie; live rotation-aware oracle potwierdza 1/1 wadliwy `solo_fallback`, a zwykły winner-first pozostaje bajt-identyczny. Mutation replay czasówka (`CS.alternative_candidates` → stary slice, kopia poza repo): `3 failed, 1 passed` — 3 testy idące przez `eval_czasowka` padają jak oczekiwano, 4. (`_format_koord_alert`) testuje inną granicę (konsumenta), przechodzi niezależnie.
rollback: `git revert <commit-2-sha> <commit-A8-2>` (odwrotna kolejność), pełna suita i jeden kontrolowany restart `dispatch-shadow` (+ ewentualnie proces czasówki, jeśli inny niż dispatch-shadow — do zweryfikowania przy release); brak migracji danych.

- `py_compile shadow_dispatcher.py tests/test_a8_alternatives_integrity_2026_07_19.py` — PASS.
- Nowe testy integralności: `8 passed`.
- Mutation probe (`result.candidates` → `result.candidates[1:]`): oczekiwane
  `5 failed, 3 passed`; po odtworzeniu fixa `8 passed`.
- Rozszerzony klaster serializery + ETA + proposal/panel: `178 passed, 1 skipped,
  1 xfailed`.
- Pełny baseline `HERMETIC_STRICT=1`: `5133 passed, 74 skipped, 7 xfailed,
  9 failed` w 305.05 s.
- Pełna regresja po zmianie: `5141 passed, 74 skipped, 7 xfailed, 9 failed`
  w 293.98 s.
- Zbiory dziewięciu failujących nodeidów są identyczne. To znany szum
  pkgroot-harnessu: `conftest_flag_strip_guard` ×3, `flag_doc_coverage` ×3 i
  trzy istniejące `script_run`. Delta: `+8 passed`, `0` nowych failów.
- `git diff --check` — PASS.

Pierwszy świeży blind review poprawnie wykrył trzy niezamknięte brzegi:
duplikat niewybranego CID, reprezentację `200` vs `200.0` i dwa różne obiekty
bez CID. Wszystkie trzy mają teraz osobne testy i wspólną implementację opartą
na `identity.schema.canon_cid`.

Kandydat po remediacji został przypięty SHA-256
`ed9f10c71dc4f009153ed3433827fc45ec9193d8f3ae6139c1e62ef7b26f3225`;
manifest bundla potwierdził `pin_verified=true`. Dwóch nowych reviewerów
(`model_tier=sol`, `effort=high`, dokładny wariant nieatestowany przez interfejs)
oceniło wyłącznie ślepy bundle pod różnymi kątami. Oba niezależne werdykty:
`CLEAN`, `findings=[]`; oba przeszły `ziomek-blind-review check`.

**Ten pin i te dwa werdykty pokrywają WYŁĄCZNIE commit 1** (bundle zbudowany
przed rozszerzeniem czasówki). Commit 2 (sekcja „A8-2b" niżej) NIE przeszedł
jeszcze przez `ziomek-blind-review` — wymagany nowy bundle na CAŁYM branchu
(oba commity) przed release. Autorska weryfikacja (ta sesja) nie zastępuje
niezależnej recenzji.

## Wydanie i rollback

Nie wykonano żadnego merge, push, deploy, restartu, zmiany flagi ani danych
runtime. Produkcja nadal emituje stary kształt do czasu osobnej bramki live.

Wydanie wymaga przez MAIN:

1. synchronizacji z aktualnym `master` i ponownej pełnej regresji na kanonie;
2. jawnego biznesowego ACK na deploy/restart `dispatch-shadow`;
3. jednego kontrolowanego restartu, health/PID/NRestarts/fingerprint oraz smoke
   świeżego `solo_fallback`/OBJM, jeśli taki przypadek wystąpi;
4. oznaczenia chwili odcięcia — historyczne wadliwe rekordy pozostają
   historyczne i nie powinny być przepisywane;
5. ponownej oceny at#220 wyłącznie na świeżym oknie po wdrożeniu albo jawnego
   opisania mieszanej jakości danych.

Rollback: `git revert <commit-A8-2>`, ponowna pełna suita i jeden kontrolowany
restart `dispatch-shadow`. Brak migracji i brak danych do cofnięcia. Awaryjny
rollback przed deployem to po prostu niewłączanie commitu.

## A8-2b — rozszerzenie: bliźniak czasówki (2026-07-19, commit 2, ta sama sesja prep)

Mandat CTO po odczytaniu tego pliku: Przykazanie #0 „bliźniacze ścieżki razem"
— fix commitu 1 celował wyłącznie w `shadow_dispatcher.py:938`, ale
`czasowka_scheduler._eval_czasowka_impl` ma STRUKTURALNIE IDENTYCZNY wzorzec
`"alternatives": result.candidates[1:]` w 6 miejscach (l. 358/367/392/401/
413/422 po fixie — wcześniej 357/366/391/400/412/421), karmiony tym samym
`core.decide._decide()`. Nieobjęty oryginalnym findingiem `05b_RUNTIME_DEEP_AUDIT`
ani mapą commitu 1, i (zweryfikowane żywo) nieobecny w `twins-registry.json` —
`ziomek-cto scope "serializer alternatives candidates best"` przed tym
commitem trafiał WYŁĄCZNIE w klasę `nowa-metryka` (inny temat, propagacja
`metrics` A/B — nie ten bug).

**Grep-sweep pełnego repo** (`grep -rn 'candidates\[1:\]' --include='*.py' .`,
wykluczone `tests/` i `eod_drafts/`): 7 trafień — 6× `czasowka_scheduler.py`
(naprawione, patrz mapa kompletności wyżej) + 1× `feasibility_v2.py:247`
(`_po_drodze_waves`, N-D — patrz mapa: inna zmienna typu `Order` nie
`Candidate`, pętla porównawcza sąsiadów, `candidates[0]` explicite
zainicjowany linijkę wyżej jako pierwsza fala, nie odcięty; brak pojęcia
best/wykluczenia). Zero 7. miejsca tej klasy.

**Wyniesienie helpera:** `_candidate_identity_key` / `_alternative_candidates`
przeniesione z `shadow_dispatcher.py` do `identity/candidate_pool.py` (nowy
plik) i zaimportowane z powrotem pod oryginalnymi nazwami — `shadow_dispatcher.py`
ma zero zmiany zachowania (potwierdzone: `git diff` na wywołaniu w
`_serialize_result` puste, zmieniły się tylko importy). Wybór lokalizacji:
`identity/` już eksportuje `canon_cid` (Faza A, wg własnego docstringu
"additive read-only... inert until Faza B" — nieaktualne od commitu 1, który
jako PIERWSZY zaimportował `canon_cid` do żywego silnika; docstring
skorygowany w tym commicie), a nowy helper jest duck-typed wyłącznie na
`.courier_id` — zero zależności od `core.candidates`/`dispatch_pipeline`, więc
zero ryzyka cyklu importu między `shadow_dispatcher.py` i `czasowka_scheduler.py`
(potwierdzone: `CS.alternative_candidates is SD._alternative_candidates is
I.alternative_candidates` — jeden obiekt funkcji, nie tekstowa kopia).
`czasowka_scheduler.py` importuje `alternative_candidates` wprost (bez aliasu,
publiczne API modułu).

**Nowe testy** (`tests/test_a8_2_czasowka_alternatives_2026_07_19.py`, 4):
mirror trzech scenariuszy z klastra shadow_dispatcher przez realny
`eval_czasowka()` (mins=35, okno FORCE_ASSIGN/KOORD — jedyne okno gdzie
`best`+`alternatives` idą z surowej, niepustej puli) + dedykowany test
`_format_koord_alert` (Telegram „Top 3 odrzuconych"), jedynego realnego
konsumenta czytającego `alternatives` wprost (nie osłoniętą
`all_candidates_for_proactive`) w ścieżce human-facing:

- `test_objm_winner_from_middle_does_not_drop_original_leader`
- `test_solo_fallback_excludes_rejected_twin_of_selected_courier`
- `test_no_solo_best_none_keeps_full_pool`
- `test_koord_alert_top3_no_loss_no_duplicate` — z fixem `cid=200` (bliźniak)
  pojawia się dokładnie raz i `cid=100` (realny odrzucony) nie znika; ze starym
  `candidates[1:]` (odtworzone przez podmianę na kopii, nie w repo) `cid=100`
  ginie, a `cid=200` dubluje się (best + bliźniak) — kontrastowy werdykt tego
  samego kuriera w jednym alercie.

**Mutation probe** (`CS.alternative_candidates` → `lambda c, b: c[1:]`, na
tymczasowej kopii testu, nie w repo): 3 failed / 1 passed — dokładnie testy
idące przez `eval_czasowka()`→`_decide` padają (jak oczekiwano), test
`_format_koord_alert` przechodzi nadal bo importuje prawdziwy helper
bezpośrednio (testuje inną granicę — konsumenta poprawnej listy, nie
producenta).

**`observability.candidate_logger` hook** w `eval_czasowka()` (l. ~205) czyta
`result.get("alternatives")` bezpośrednio, defensywnie (`try/except pass`,
flagowany `_flag_check()`) — kształt naprawiony u źródła w
`_eval_czasowka_impl`, ćwiczony (nie asercjonowany osobno) w 4 nowych testach
via `eval_czasowka()`; brak osobnej regresji potrzebnej.

**`czasowka_proactive/{score_selector,evaluator}`**: oba wolą
`all_candidates_for_proactive` (pełna, nieciachana lista — inny klucz, bez
zmian). `evaluator._build_candidate_list` spada na `best+alternatives` tylko
gdy `CZASOWKA_PROACTIVE_USE_ALL_CANDIDATES=False` (kod default; `common.py:2479`);
w obu wariantach dostaje teraz kompletną, naprawioną listę — N-D niezależnie
od stanu flagi.

**`twins-registry.json`**: nowa klasa `alternatives-best-identity` (5 miejsc:
helper, oba producenty, oba pliki testów). Zweryfikowane żywo po dodaniu:
`ziomek-cto scope "serializer alternatives candidates best"` teraz zwraca 2
klasy zamiast 1, wszystkie 3 symbole kodu `OK` (żywy grep w driverze).

**Regresja (mój własny, spójny pomiar — nie mieszam z liczbami autora commitu 1
powyżej, które pochodzą z innego środowiska/uruchomienia wcześniej tego dnia):**

- Przed commitem 2 (tylko commit 1, mój pomiar via szeroki `-k 'serializer or
  alternatives or calib or a8'` — w praktyce ~cała suita, te słowa kluczowe są
  pojemne): `5138 passed, 9 failed, 77 skipped, 7 xfailed` (5231 total).
- Po commicie 2 (`pytest tests/` — PRAWDZIWIE pełna suita, bez `-k`):
  `5142 passed, 9 failed, 77 skipped, 7 xfailed` w 302.90s (5235 total).
- Delta: `+4 passed` (dokładnie 4 nowe testy czasówki), `0` nowych failed,
  `0` nowych skipped, `0` nowych xfailed. **9 failed = te same nodeidy co
  przed commitem 2** (`conftest_flag_strip_guard`×3, `flag_doc_coverage`×3,
  3×`script_run`: `test_f4_courier_pos_pickup_proxy`/
  `test_panel_aware_availability`/`test_panel_packs_bag_reconstruction`) —
  zweryfikowane linia-po-linii w obu logach, identyczne.
- Dedykowany klaster obu plików A8-2 razem: `12 passed in 1.57s`.
- `py_compile shadow_dispatcher.py czasowka_scheduler.py
  identity/candidate_pool.py identity/__init__.py` — PASS.
- Import smoke (z neutralnego cwd, żeby uniknąć pułapki
  `lekcja-ziomek-scripts-root-import`): `dispatch_v2.identity`,
  `czasowka_scheduler`, `shadow_dispatcher` wszystkie ładują się z worktree
  (`/root/wt-audit-a8-pkgroot/...`), zero cyklu importu.

## Pozostały P1

C3-01 nie jest częścią tego commitu. Wymaga osobnego sprintu obejmującego
`reconciliation/auto_resync.py` oraz wszystkie bliźniacze ścieżki
`emit/emit_audit → update_from_event` w `panel_watcher.py`. Dedup zapisu eventu
nie może blokować ponowienia aplikacji do `orders_state`; statystyka sukcesu nie
może rosnąć po nieudanym apply. To szersza zmiana gorącego writera i nie wolno
jej doklejać do minimalnego fixa serializera.
