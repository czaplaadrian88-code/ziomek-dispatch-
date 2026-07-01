# C11 — ORACLE: `ziomek_time_route_monitor` (parytet konsola↔apka czas/trasa)

**Lane C (RUNTIME-ORACLE, C9/C11). READ-ONLY. Sesja tmux 2. 2026-06-30 ~17:25 UTC.**
Agent: C11-time-route-monitor. Werdykt: **VALIDATED** (z 6 udokumentowanymi caveatami fidelity/semantyki).
Wszystkie `plik:linia` z świeżego grep/read dziś. Drugie metody policzone NIEZALEŻNIE; output narzędzi → scratchpad (NIC do `dispatch_state/`).

---

## 0. TL;DR

`ziomek-time-route-monitor` (timer 10 min, venv panelu) co tick liczy DWIEMA drogami kolejność stopów każdego worka:
- **KONSOLA** = `fleet_state._build_route` (panel) z `flag("TRUST_CANON_ORDER")` (env `PANEL_FLAG_TRUST_CANON_ORDER=1`),
- **APKA** = `route_podjazdy.order_podjazdy(bag, plan, plan_aware, trust_canon)` (dispatch_v2),
i raportuje `q3_route_match/checked` + `q3_route_mismatches`. Seed: „44-75 rozjazdów/dzień".

**Werdykt: VALIDATED.** Świeży jsonl (1621 ticków, 06-19→06-30, mtime 17:12 FRESH) pokazuje **0 mismatchy od 06-29**. Moja niezależna re-komputacja (3. metoda = `canon_direct` renderowany WPROST z `plan.stops`/p.sequence, z pominięciem obu rendererów) zgadza się `con==app==canon_direct` na **9/9 żywych workach (cov=covered)**, **deterministycznie** (pass1≡pass2 + świeży proces), ZERO fikcyjnych pickupów, ten sam zbiór stopów. Adversarial F4 dowodzi że porównanie **MA ZĘBY** (rozróżnia relax↔carried-first, NIE jest tautologią). Spadek do 0 = **LEGALNY FIX U ŹRÓDŁA** (apka przełączona carried-first→kanon commitem `61381ac`+restart courier-api 06-28 12:50:51), NIE „kalibracja do ciszy".

**Co flipuje:** decyzję „stop/extend monitora regresji" (review tool: clean→`disable timer`) + jest JEDYNYM runtime-parytetem cross-repo konsola↔apka (twin #11 / root R2 „one route-order module" — brak wspólnego importu, A5/A6).

**Caveat-rdzeń:** monitor mierzy **RÓWNOWAŻNOŚĆ DWÓCH RENDERERÓW na kanonie**, nie fizyczną kolejność jazdy; jego wierność do produkcji opiera się na CZYTANIU TEKSTU DROP-INÓW (nie efektywnego configu) → strukturalnie nie wykrywa stanu martwa-flaga/nie-zrestartowany/brak-konsumenta (klasa E/D latentna; uratowany 06-28 tylko przypadkiem timingu).

---

## 1. CO MONITOR LICZY (świeży read)

Plik: `nadajesz_clone/panel/backend/tools/ziomek_time_route_monitor.py`.
- `route_console` (`:203-209`) → `_build_route(plan_doc, bag, None, {oid:{}})` → `[(s.type, tuple(order_ids))]`.
- `route_app` (`:212-221`) → `RP.order_podjazdy(bag, plan_doc, plan_aware, trust_canon)`.
- pętla q3 (`:285-297`): per worek `con=route_console`, `app=route_app`; `route_match += (con==app)`; inaczej `route_mismatches.append`.
- mirror flag produkcji (KLUCZOWE dla fidelity):
  - `_app_route_flag_on` (`:121-126`) czyta `courier-api.service.d/podjazdy.conf` (ENABLE_APP_ROUTE_FROM_CONSOLE) — **tylko do anomalii `app_flag_off`, NIE przekazywane do route_app**.
  - `_plan_aware_flag_on` (`:129-138`) czyta `plan-aware-podjazdy.conf` → `plan_aware`.
  - `_build_view_trust_canon_flag_on` (`:141-150`) czyta `build-view-trust-canon.conf` → `trust_canon`. Docstring `:145` jawnie woła „Wzorzec #15".
- KONSOLA trust_canon: monitor.service ma drop-in `trust-canon.conf` → `Environment=PANEL_FLAG_TRUST_CANON_ORDER=1`; `flag()` (panel `app/core/flags.py:127-129`) czyta `os.getenv("PANEL_FLAG_TRUST_CANON_ORDER")` w CZASIE WYWOŁANIA → True.

## 2. FIDELITY DO PRODUKCJI (zweryfikowana, nie założona)

| Powierzchnia | Produkcja (efektywny stan) | Monitor | Wierność |
|---|---|---|---|
| APKA route | `courier_orders.build_view:1116-1120`: branch `APP_ROUTE_FROM_CONSOLE` woła `order_podjazdy(list(mine.values()), plan, plan_aware=config.PLAN_AWARE_PODJAZDY, trust_canon=config.BUILD_VIEW_TRUST_CANON_ORDER)`. Efektywny env courier-api: **wszystkie 3 flagi =1** (`systemctl show`), serwis active. | `route_app(plan_aware=True, trust_canon=True)` (mirror z drop-inów) | **WYSOKA** — identyczne wywołanie tej samej funkcji z tymi samymi flagami |
| KONSOLA route | `fleet_state._build_route` z `flag("TRUST_CANON_ORDER")`; nadajesz-panel env `PANEL_FLAG_TRUST_CANON_ORDER=1` + `TRUST_CANON_WHEN_COVERS_BAG=1`. Realny caller `fleet_state:874-880` liczy `trust_canon_ok` przez `_resolve_invalidated_plan`. | `_build_route(..., trust_canon_ok=DEFAULT True)` | **WYSOKA dla planów ważnych I invalidated** — bo panel `TRUST_CANON_WHEN_COVERS_BAG=1` → `_resolve_invalidated_plan` zwraca `trust_canon_ok=True` (`:386-388`) = ten sam stan co default monitora |

C5 near-miss (A6) NIE dotyczy bieżącego stanu: `BUILD_VIEW_TRUST_CANON_ORDER` ma DWÓCH konsumentów — `:1120` (parametr do order_podjazdy, **ŻYWY** gdy APP_ROUTE_FROM_CONSOLE=1) i `:1158` (guard reorder, martwy bo `_console_done=True` go omija). Monitor mirroruje konsumenta ŻYWEGO. ✅

## 3. ORACLE #1 — niezależna re-komputacja na ŻYWEJ próbie

Skrypt `scratchpad/c11_oracle.py` (venv panelu). 3. metoda `canon_direct` (`:74-103`) renderuje sekwencję WPROST z `plan_doc["stops"]` (p.sequence), aplikując reguły z docstringów (skip picked_up-pickup, scal kolejne same-restaurant, dedup dropów, bramka pokrycia) — NIE woła ani `_build_route` ani `order_podjazdy`. Inwarianty `:106-136`.

**Wynik (2 passy + świeży proces, identyczne):**
```
route_checked=9 route_match=9 mismatches=0    DETERMINISM identical: True
coverage distribution: {'covered': 9}
bags where trust_canon flips app: 0/9         ANY invariant issue: False
```
- `con==app` na 9/9 (zgodne z monitorem: last tick 06-30 17:22 = 10/10, 0 mism, 0 err; różnica 9 vs 10 = dryf żywego orders_state między odczytami, oba 100%).
- 3. metoda `canon_direct == con == app` na WSZYSTKICH 9 covered → trójkąt się domyka.
- Tripwire'y: ZERO fikcyjnych pickupów (każdy pickup-oid pick-eligible), `set(con)==set(app)==bag`, kolejność z p.sequence = z rendererów. **PASS.**
- Próbka: cid 531(n3)/370(n3)/179(n4)/376(n2)/515/484/447/441 + 207(n1), wszystkie plan-covered.

## 4. ORACLE #2 — adversarial: czy `con==app` MA ZĘBY (czy to tautologia)

Skrypt `scratchpad/c11_adversarial.py`. Bo na żywej próbce `trust_canon flips app = 0/9` (relax nic nie zmienia na tych workach) — sama 0-próbka NIE dowodzi że instrument cokolwiek wykryje. Syntetyczne worki wymuszające klasy rozjazdu:

| Fixture | con==app(ON) | relax flips app (ON vs OFF) | Wniosek |
|---|---|---|---|
| **F4** carried+relax interleave (kanon: pickB, dropA-carried, dropB, pickC) — **DOKŁADNIE klasa 44-75/d** | **True** | **True** | app(OFF)=front-load carried `dropA` PIERWSZY → ≠ console(canon relax). **MA ZĘBY** |
| F1 same-rest merge (P1,P2 Sushi) | True | False | merge zgodny obu stronom |
| F3 drop-order = plan-rank (R1 przed R2 mimo późn. ck) | True | False | dedup+rank zgodne |
| F2 plan PARTIAL (Q2 absent → fallback obu) | True (con==app) | — | fallback-twins (osobne impl.) zgodne NA TYM case |

**F4 = dowód kluczowy:** porównanie `con==app` ROZRÓŻNIA relax↔carried-first. Gdyby monitor liczył `app` z `trust_canon=OFF` (stan sprzed `e3d42fd`), pokazałby MISMATCH dokładnie tej klasy. Czyli `e3d42fd` zrównał monitor z NAPRAWIONĄ apką, NIE uciszył realny rozjazd. Historyczne mismatche `con=[pickup,dropoff,pickup,dropoff]` vs `app=[pickup,pickup,dropoff,dropoff]` (jsonl 06-22 cid 75) = ten wzorzec.

## 5. TIMELINE jsonl — spadek do 0 = FIX U ŹRÓDŁA (nie cisza)

```
day        ticks checked match  MISM mism_ticks  tc_field tc_true  errs
06-19..21    366   1665  1665      0       0          0       0       0
06-22        143    369   360      9       6          0       0       0   <- carried-first relax wchodzi na konsolę 22.06
06-23        143    469   357    112      54          0       0       0   <- PEAK rozjazdu (apka carried-first, konsola kanon)
06-24..27   ...    ...   ...   41/58/75/44 ...        0       0   69-144  <- seed „44-75/d"; errs=plan_aware mirror 06-24
06-28        144    625   595     30      18         66      66     144   <- tc_field pojawia się 12:59 (e3d42fd)
06-29        144    520   520      0       0        144     144     144   <- 0 mismatchy
06-30        104    442   442      0       0        104     104     104
```
Archeologia commitów/restartów (precyzyjna):
- `61381ac` (courier_api: build_view `:1120` przekazuje trust_canon — „odzywa martwą flagę C5") commit **06-28 12:50:19** → restart courier-api **06-28 12:50:51** (32 s później) → **produkcyjna apka renderuje kanon od 12:50:51**.
- `e3d42fd` (monitor: route_app mirror trust_canon) commit **06-28 12:59:49** — 9 min PO realnym fixie produkcji.
- `build-view-trust-canon.conf` mtime 06-22 18:40, ALE konsument `:1120` nie istniał do 06-28 → flaga była **MARTWA (C5)** 06-22..06-28 → apka prod = carried-first → **mismatche 06-22..06-28 były PRAWDZIWE** i uczciwie raportowane.

Wniosek: instrument był UCZCIWY przez cały okres; 0 dziś = realna parytet w produkcji (apka faktycznie przełączona u źródła).

## 6. INSTANCJE / SMELLE (plik:linia + klasa)

| # | Klasa | plik:linia | Opis | sev | open |
|---|---|---|---|---|---|
| C11-1 | **E/J** | `ziomek_time_route_monitor.py:141-150` (`_build_view_trust_canon_flag_on`) + `:129-138` | Fidelity opiera się na CZYTANIU TEKSTU drop-inu, nie efektywnego configu serwisu. NIE wykrywa: martwa-flaga / nie-zrestartowany / brak-konsumenta. **Dowód:** `build-view-trust-canon.conf` zawierał `=1` od 06-22 18:40, prod apka IGNOROWAŁA do 06-28 12:50 (C5). Gdyby mirror istniał w tym oknie → czytałby True → MASKOWAŁby realny rozjazd. Uratowany TYLKO timingiem (mirror dodany 9 min po realnym fixie). Self-ack jako „Wzorzec #15". Manualny mirror 2-repo = nawracająca kruchość | P2 | TAK |
| C11-2 | **F** | `:269-297` (q3) + service `:15` docstring „KONSOLA==APKA" | q3 mierzy RÓWNOWAŻNOŚĆ DWÓCH RENDERERÓW na kanonie, NIE fizyczną kolejność jazdy. Brak join GPS/`delivered_at`/`gps_delivery_truth`. „0 mismatchy" odpowiada „czy renderery się zgadzają", NIE „czy trasa jest poprawna / czy kanon jest dobry". Nazwa może być nad-czytana | P2 | TAK |
| C11-3 | **B/J** | konsola fallback `fleet_state.py:451-464` vs apka fallback `route_podjazdy.py:210-232` | Gdy kanon NIE pokrywa worka → obie strony spadają do OSOBNYCH implementacji carried-first (parytet tylko golden-test, brak wspólnego importu). Na żywej próbce 9/9 = covered → **ścieżka fallback NIE jest ćwiczona live**; mój F2 pokazał zgodność na 1 case, ale to krucha gałąź | P3 | TAK |
| C11-4 | **J (coverage)** | `:179-200` (`build_bags`) vs `courier_orders.build_view` `mine` | Monitor buduje worki z `orders_state` ACTIVE_STATES; produkcja `build_view` buduje `mine` własnym resolverem. Równość członkostwa worka NIE zweryfikowana → monitor może renderować INNY worek niż produkcja | P3 | TAK |
| C11-5 | **H** | `ziomek_time_route_review.py:main` (`q3_mismatch=[...for t in ticks...]`, `clean`) | Review agreguje `q3_route_mismatches` po CAŁYM jsonl od 06-19 (brak okna czasowego) → `clean` NIGDY już nie będzie True (realne mismatche 06-22..06-28 zostają w pliku na zawsze) → zawsze „PRZEDŁUŻYĆ", nie rozróżnia rozwiązany-historyczny↔bieżący | P3 | TAK |
| C11-6 | **M** | `:357-361` (`except OSError: pass`) | Zapis raportu połyka błąd I/O → cichy ubytek ticku (spójne z A4 §8 M `_append_jsonl`-swallow). Mniej krytyczne (1 tick/10 min) | P3 | TAK |

## 7. INWARIANTY-TRIPWIRE (wynik)

| Inwariant | Wynik |
|---|---|
| `con == app` (claim monitora) | ✅ 9/9 live + zgodne z last-tick 10/10 |
| 3. metoda `canon_direct(p.sequence) == con == app` (covered) | ✅ 9/9 |
| ten sam zbiór + liczba stopów | ✅ `set(con)==set(app)==bag` |
| ZERO fikcyjnych pickupów odebranych | ✅ każdy pickup-oid pick-eligible (status≠picked_up) |
| kolejność z p.sequence (nie sort-ts) | ✅ canon_direct iteruje plan.stops bezpośrednio |
| determinizm (≥2 odpalenia) | ✅ pass1≡pass2 + świeży proces |
| MA ZĘBY (nie tautologia) | ✅ F4 relax-flip dowodzi rozróżnialności |

## 8. PROXY vs GROUND-TRUTH

`con`/`app`/`canon_direct` to TRZY renderingi tego samego `courier_plans.json` — **NIE fizyczna kolejność jazdy**. Brak join `gps_delivery_truth.jsonl`/`delivered_at`. Werdykt = **PROXY-CERTIFIED** (równoważność rendererów planu), nie ground-truth fizyczny. Caveat fundamentu A4 §7 dotyczy: instrument w ogóle nie patrzy na delivered_at; mierzy zgodność dwóch interpretacji planu.

## 9. DEKLARACJA POKRYCIA

**Zbadane (świeży read/grep/oracle dziś):**
- `ziomek_time_route_monitor.py` (cały, 399 L) — q1/q2/q3, mirror-flagi, build_bags, route_console/route_app.
- `ziomek_time_route_review.py` (verdict aggregation).
- `route_podjazdy.py` (cały, 233 L) — order_podjazdy/_canon_order_from_plan/pickup_runs/plan_drop_rank.
- `fleet_state.py:330-518` — _order_from_plan_seq, _resolve_invalidated_plan, _build_route (kanon+fallback).
- `courier_orders.py:1100-1194` — build_view branche (APP_ROUTE_FROM_CONSOLE/ziomek_plan/fallback) + config.py:38-72.
- Efektywny env: courier-api (3 flagi=1, active), nadajesz-panel (PANEL_FLAG_TRUST_CANON_ORDER+WHEN_COVERS_BAG=1), monitor.service (drop-in trust-canon).
- jsonl 1621 ticków (timeline mismatchy/dni + pierwsze pojawienie tc/pa field).
- Archeologia: git log 61381ac/e3d42fd/6015320/5b57bb7/57df89c + restarty courier-api (journal 06-28..06-29).
- Oracle: 9 żywych worków (2 passy + świeży proces) + 4 syntetyczne fixture'y (F1-F4).

**NIE zbadane (jawne luki):**
1. **Ścieżka fallback (kanon nie-pokrywa) NA ŻYWO** — 9/9 covered w próbce; osobne impl. fleet_state:451-464 vs route_podjazdy:210-232 ćwiczone tylko syntetycznie (F2). Faza B/C: dłuższe okno / wymuszony partial-plan.
2. **Równość worka monitor↔produkcja** (`build_bags` vs `mine`) — nie zdiffowane na realnym kurierze; możliwy rozjazd członkostwa (C11-4).
3. **q1/q2 (czasy odbioru przekazane/stałe)** — poza zakresem tego oracle (route-focus); jsonl pokazuje q1_missing=0/q2_drift=0 całość, ale nie re-walidowałem 2. metodą.
4. **Apka Kotlin** (`RouteLogic.kt`) — render serwerowy (stopSequence z build_view), ale lokalny re-sort/ETA nie zweryfikowany kodem (A6 luka 1; granica = courier_orders pokrywa kolejność).
5. **Telegram-send `_telegram`** nie odpalany (read-only, by-design).

**Granica:** STOP na dyspozytorni — bez Mailek/Papu. Output narzędzi: scratchpad (`c11_oracle.py`, `c11_oracle_out.json`, `c11_adversarial.py`); ZERO zapisu do dispatch_state.
