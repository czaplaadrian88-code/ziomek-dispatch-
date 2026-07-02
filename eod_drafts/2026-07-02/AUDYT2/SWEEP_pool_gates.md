# AUDYT 2.0 — SWEEP: BRAMKI PULI I ESKALACJI (`manual_overrides.py` + `auto_koord.py`)

**Data:** 2026-07-02 | **Tryb:** READ-ONLY wobec produkcji (zero edycji kodu/flag/systemctl; analizy na kopiach) | **Lane:** pool gates & escalation
**Pliki źródłowe:** `dispatch_v2/manual_overrides.py`, `dispatch_v2/auto_koord.py`
**Powiązane (skonsumowane w audycie):** `courier_resolver.py:1436-1512`, `nadajesz_clone/panel/backend/app/integrations/ziomek/{fleet_state.py,courier_block.py}`, `panel_watcher.py:1376-1398`, `observability/koord_cascade_monitor.py`, `manual_overrides_daily_reset.py`, `dispatch_pipeline.py:2614-2627/3535-3540`

---

## 0. STRESZCZENIE (dla człowieka, prostym językiem)

**Co robią te dwa pliki (bramki puli i eskalacji):**
- `manual_overrides.py` = ręczne wykluczanie/dodawanie kurierów („/stop Bartek", „Adrian pracuje"). Trzyma listę w `manual_overrides.json`, kasowaną codziennie o 06:00. To najtwardsza bramka puli: kto tu wpadnie na `excluded`, tego Ziomek NIE proponuje.
- `auto_koord.py` = automatyczna eskalacja czasówek (odbiór ≥ 60 min) do Koordynatora (cid=26) w chwili pojawienia się zlecenia. **Wbrew docstringowi („default False") jest WŁĄCZONY na żywo** (`AUTO_KOORD_ON_NEW_ORDER_ENABLED=True`), działa od 2026-05-05, 1057 udanych przypisań.

**Zdrowie (potwierdzone pomiarem):** oba timery systemd zdrowe 14/14 dni (`overrides-reset`, `koord-cascade` — zero failów). Podwójna eskalacja zabezpieczona dwuwarstwowo (dowód: 7× `race_avoided_assigned_to_26` w logu). Polityka „zawsze proponuj" trzyma — 01.07 wszystkie 13 werdyktów KOORD to `early_bird` (czasówki), zero kaskady saturacyjnej.

**Najgroźniejsze (do naprawy — szczegóły niżej):**
1. **Konsola i silnik NIE zgadzają się kogo blokują** — silnik blokuje po nazwie LUB cid, konsola tylko po nazwie. Fix z 10.06 wpięty w jeden bliźniak. Koordynator może widzieć jako „dostępnego" kuriera, którego Ziomek cicho pomija.
2. **Sklep wykluczeń bez locka, 3 żywych pisarzy** (Telegram + konsola + reset) → możliwe ciche nadpisanie wykluczenia (lost-update).
3. **Fail-OPEN w ciszy** — uszkodzony `manual_overrides.json` = wszystkie „/stop" znikają bez alertu, tylko log.

---

## 1. `manual_overrides.py` — pełny sweep

### 1.1 Format store
Plik: `dispatch_state/manual_overrides.json`. 4 klucze:
```json
{"excluded": ["Bartek O.", ...],           // NAZWY (panel-nick/kanon)
 "excluded_cids": ["123", ...],            // CID (str) — zalążek B, 2026-06-10
 "working": {"<cid>": {"start","end","end_explicit","name","added_at"}},  // working-override, cid-keyed
 "updated_at": "<iso UTC>"}
```
`load()` samo-uzupełnia brakujące klucze (setdefault) i twardnieje typy (list/dict). Stan na dziś (kopia): wszystkie 4 puste, `updated_at=2026-07-01T07:25Z` (po porannym resecie, żaden ręczny override od tego czasu).

### 1.2 Kto pisze (writerzy)
**TRZECH żywych pisarzy tego samego pliku — read-modify-write:**
| Pisarz | Proces | Ścieżka |
|---|---|---|
| Telegram | `dispatch-telegram` (async `to_thread`) | `telegram_approver.py:2570/2652/3011 → manual_overrides.parse_command` (`/stop`, `/wraca`, „X nie pracuje", „X pracuje", „reset") |
| Konsola gps | panel backend → **subprocess** świeżego pythona Ziomka | `nadajesz_clone/.../courier_block.py:_BLOCK_PYCODE → mo._do_exclude / _do_include` (przycisk 3-stanowy 🔴/🟠/🟢) |
| Reset dzienny | `dispatch-overrides-reset` 06:00 | `manual_overrides_daily_reset.py` (czyści 3 klucze + coordinator_activations) |

Konsola pisze przez subprocess do KANONICZNEGO `dispatch_v2.manual_overrides` (Z3: jedno źródło logiki zapisu — dobre). Reset i konsola używają tych samych prymitywów `_do_exclude`/`_do_include`.

### 1.3 Reset dzienny (timer)
- `dispatch-overrides-reset.timer` → `OnCalendar=*-*-* 06:00:00 Europe/Warsaw`, `Persistent=true`. **AKTYWNY.** Ostatnie 14 dni w journalu: **14/14 „Deactivated successfully"** (zero failów). Ostatni: Jul 01 04:00:00 UTC = 06:00 Warsaw.
- Drop-iny (dobrze uzbrojony): `onfailure.conf` (OnFailure→alert), `cron_health_success.conf` (ExecStopPost ledger last_success), `resource_limits.conf`.
- Skrypt `manual_overrides_daily_reset.py`: czyści `excluded`+`excluded_cids`+`working`, potem `coordinator_activations` (P4). **Fail-loud na uszkodzonym pliku** (`JSONDecodeError/OSError → exit 1 → OnFailure alert`), fail-soft na braku pliku (exit 0). Atomic write.
- Dowód działania (log resetu, ostatnie przebiegi): codziennie czyści 2-3 nazwy + parytetowe `excluded_cids` (`Bartek O.`/123 wraca KAŻDEGO dnia — blokowany jako kurier i resetowany rano). `working` zawsze `[]` w obserwowanych przebiegach → feature working-override realnie nieużywany w tym oknie.

### 1.4 TTL / wygasanie wpisów
**Brak per-wpis TTL.** Lifecycle „do końca dnia" egzekwuje WYŁĄCZNIE `dispatch-overrides-reset` (06:00). Pojedynczy wpis nie ma daty wygaśnięcia — żyje aż reset skasuje CAŁY plik. To pojedynczy punkt awarii: wyłączenie/awaria timera = wpisy persystują bez końca. **To dokładnie incydent 03-07.05** (13 nazw persystowało 4 dni, w tym top-performerzy — udokumentowane w docstringu skryptu resetu). Dziś zdrowe (14/14), ale bez drugiego bezpiecznika (np. sanity „updated_at starszy niż X h").

### 1.5 Plik uszkodzony / pusty → fail-open czy fail-close?
**FAIL-OPEN, w ciszy.** `load()` (`:38-56`) łapie każdy wyjątek → `d={}` → `excluded=[]`. Konsument `courier_resolver.py:1445-1449` dodatkowo owija w try/except → `excluded=set()`, `excluded_cids=set()`, tylko `_log.warning("manual_overrides load failed")`. Znaczenie: **uszkodzony store = WSZYSTKIE wykluczenia znikają → Ziomek zaczyna proponować kurierów, których Adrian ściągnął (/stop) — bez żadnego alertu.** Świadomy wybór (dostępność dispatchu > blokada), ale runtime nie ma sygnału operacyjnego (reset-cron ma OnFailure, lecz to inny moment doby).

### 1.6 Konsumenci (silnik + konsola) — PARYTET INTERPRETACJI ⚠
| Konsument | Co czyta | Jak egzekwuje |
|---|---|---|
| **Silnik** `courier_resolver.dispatchable_fleet:1504-1512` | `get_excluded()` (nazwy) **+ `get_excluded_cids()` (cid)** + `get_working()` | blokuje gdy `cs.name in excluded` **LUB** `str(cid) in excluded_cids` (flaga `ENABLE_EXCLUDE_BY_CID=True`) |
| **Konsola** `fleet_state.read_fleet:752/884` | `_load_excluded()` = tylko `excluded` (nazwy); `_load_working()` = `working` (cid) | `blocked = canon_name[cid] in excluded` — **TYLKO po nazwie, NIE czyta `excluded_cids`** |

**Rozjazd (finding SPG-02):** fix `excluded_cids` (10.06, po to by blokada trzymała mimo desync nick↔pełne-imię) wpięty w JEDEN bliźniak (silnik). Konsola pozostała na czystym match nazw → gdy nazwa w `excluded` różni się od nazwy kanonicznej konsoli dla tego cid, silnik blokuje po cid, a konsola pokazuje kuriera jako dostępnego. (Parytet dla `working` jest OK — obie strony po cid.)

### 1.7 Współbieżność zapisu ⚠
`save()` (`:59-67`) = temp+fsync+os.replace — atomowy WOBEC rozdarcia pliku, ale **ZERO flock/fcntl** (potwierdzone grepem: brak `flock/fcntl/LOCK_` w `manual_overrides.py` i skrypcie resetu). Przy 3 żywych pisarzach (§1.2) klasyczny lost-update: proces A czyta `{X}`, proces B czyta `{X}`, A dopisuje Y→zapis `{X,Y}`, B zdejmuje X→zapis `{}` — ostatni wygrywa, zmiana A ginie. Ta sama sygnatura co cytowany w design-doc `pending_proposals.json 3-writer no-lock` (klasa O).

---

## 2. `auto_koord.py` — pełny sweep

### 2.1 Stan LIVE (⚠ docstring nieaktualny)
Docstring `:17` deklaruje `AUTO_KOORD_ON_NEW_ORDER_ENABLED (default False)`. **flags.json: `True`.** Feature LIVE od 2026-05-05. `AUTO_KOORD_TELEGRAM_INFO_ENABLED=False` → działa cicho (bez info na Telegram). Konsument: `panel_watcher.py:1378-1398` w gałęzi NEW_ORDER.

### 2.2 Klasyfikacja czasówki (`is_czasowka`) i DUPLIKACJA progu 60
`is_czasowka(prep) = int(prep) >= 60` (`CZASOWKA_THRESHOLD_MIN=60`, semantyka `>=` = „hard rule per Adrian"). **Ten sam predykat „czy czasówka" istnieje w ≥8 niezależnych implementacjach z 5 różnymi stałymi + hardcode** (klasa N/A1):

| Miejsce | Stała / literał | Operator | Uwaga |
|---|---|---|---|
| `panel_client.py:53/692` | `CZASOWKA_THRESHOLD_MIN=60` | `>=` | ustawia `order_type` |
| `auto_koord.py:32/49` | `CZASOWKA_THRESHOLD_MIN=60` (2. NIEZALEŻNA kopia) | `>=` | eskalacja |
| `auto_proximity_classifier.py:104/160` | `CZASOWKA_PREP_MIN=60` | `>=` | Faza 7 |
| `daily_briefing.py:123` | `_CZASOWKA_PREP_MIN=60.0` | — | briefing |
| `common.py:439` (+`dispatch_pipeline.py:2617` re-export) | `EARLY_BIRD_THRESHOLD_MIN=60` (env+flags) | `>=` | early-bird KOORD |
| `state_machine.py:130` | hardcode `>= 60` | `>=` | `_is_czasowka_order` |
| `panel_watcher.py:841/1100/2186` | hardcode `>= 60` ×3 | `>=` | 3 kopie |
| `auto_assign_gate.py:128` | hardcode `>= 60.0` | `>=` | G4 |

**Semantyka DZIŚ spójna** (wszędzie `>=`, wartość 60) → brak aktywnego rozjazdu. Ale zmiana progu (np. Adrian chce 45/90) wymaga edycji ~8 miejsc = gwarantowany przyszły rozjazd. **Kolizja magic-60 (klasa L):** `czasowka_proactive/evaluator.py:292 CZASOWKA_MIN_PROPOSAL_SCORE=60` to PUNKTY score, nie minuty — to samo „60", inne pojęcie. Jedyny konfigurowalny helperem jest early-bird (`_early_bird_threshold_min()` czyta flags.json hot) — dobry wzorzec, którego reszta nie stosuje.

### 2.3 `early_bird` KOORD — semantyka i live
`dispatch_pipeline.py:3535`: `if minutes_ahead >= _early_bird_threshold_min()` (=60). Uwaga: to inna WIELKOŚĆ niż czasówka (`minutes_ahead` odbioru vs `prep_minutes`), lecz numerycznie ta sama 60 (czasówka ≥60 prep jest z definicji ≥60 min ahead). To DOMINUJĄCA żywa eskalacja: 01.07 wszystkie 13/13 KOORD = `early_bird (65..321 min ahead)`. Short-circuit odpala PRZED budową puli feasibility (klasa C2-adjacent) — ale to ŚWIADOMY, instrumentowany design (`EARLYBIRD-01` forward-shadow mierzy kontrfaktyk „co gdyby przepuścić do feasibility", flaga OFF default).

### 2.4 Serwis `dispatch-koord-cascade` — co to jest (⚠ NIE żywy eskalator)
**To NOCNY MONITOR REGRESJI, nie live cascade.** `koord_cascade_monitor.py`: raz na dobę (`OnCalendar=03:00 UTC`) liczy z wczorajszego `shadow_decisions.jsonl` werdykty KOORD z `reason startswith all_candidates_low_score` I `pool_feasible>=1`. Przy `ENABLE_ALWAYS_PROPOSE_ON_SATURATION=ON` ma być ~0; `>0` = regres polityki „zawsze proponuj best-effort" (bramka `dispatch_pipeline.py:5313` znów wpycha w ciszę mimo feasible). Alert priority=low → cichy bot @DajeszBot + kafel „Powiadomienia" (nie spamuje głównego Telegrama), dedup per-dzień. Exit 0 na każdej normalnej ścieżce; non-zero TYLKO gdy monitor sam się wywali. **Journal 14/14 czysty.** Referencja pola do bramki live: `verdict==KOORD ∧ reason~all_candidates_low_score ∧ pool_feasible_count>=1`.

### 2.5 Podwójna eskalacja / eskalacja już-przypisanego
Zabezpieczone DWUWARSTWOWO:
1. **Decyzja** `needs_auto_koord.is_unassigned`: `id_kurier` w `(None,"",0,"0")` → unassigned; inaczej `int(cid)==0` → już przypisany (w tym cid=26 Koordynator → NIE re-eskaluje).
2. **Wykonanie** `perform_auto_koord` pre-fetch (`fetch_details_fn`): re-fetch przed przypisaniem; jeśli w międzyczasie ktoś przypisał/anulował → skip `race_avoided_assigned_to_<cid>` / `race_avoided_cancelled`.

**Dowód działania (log 1065 wpisów):** `AUTO_KOORD_ASSIGNED=1057`, reason `ok=1057`; 8 „failów" = 7× `race_avoided_assigned_to_26` + 1× `race_avoided_assigned_to_21` (poprawne idempotentne skipy). **Zero `all_retries_exhausted`.** Dedup na poziomie triggera: hook w gałęzi `if result:` (NEW_ORDER emitowany raz per zlecenie) → nie re-odpala co tick.

### 2.6 Interakcja z always-propose i czasowka_scheduler
- `auto_koord` (chwila NEW_ORDER) PARKUJE czasówkę u Koordynatora (26). `czasowka_scheduler` później BUDZI ją proaktywnie w T-60/50/40 (skanuje `id_kurier=26 ∧ prep≥60`). **Komplementarne, nie konfliktowe** (klasa I: brak żywego konfliktu).
- KOORD share 2.9-6.8%/dzień (27.06-01.07), zdominowany przez `early_bird`. Kaskada saturacyjna = 0 na 01.07 → always-propose trzyma, koord-cascade monitor = 0 alertów. Spójne.

---

## 3. ORACLE (read-only, stan na żywo)

| Sonda | Wynik |
|---|---|
| Store overrides (kopia `mo_copy.json`) | Pusty: `excluded=[] excluded_cids=[] working={}`, `updated_at=2026-07-01T07:25Z`. Zero zaległych wpisów. |
| `overrides-reset` journal 14 dni | **14/14 Deactivated successfully** (exit 0). Ostatni Jul 01 04:00 UTC. |
| `koord-cascade` journal 14 dni | **14/14 Deactivated successfully** (exit 0). Ostatni Jul 01 03:00 UTC. |
| `auto_koord_log.jsonl` | 496 KB / 1065 wpisów, LIVE od 2026-05-05. `ASSIGNED=1057 (ok)`, `race_avoided=8`, `all_retries_exhausted=0`. Ostatni 2026-07-01T17:26. ~10-32/dzień. |
| Shadow ledger — werdykt KOORD (5 dni) | 27.06=10 (4.6%) / 28.06=8 (2.9%) / 29.06=16 (6.8%) / 30.06=11 (4.8%) / 01.07=13 (5.1%). |
| KOORD reasons 01.07 | **13/13 = `early_bird (65..321 min ahead)`. Zero `all_candidates_low_score`** (kaskada = 0). |
| Reset-log parytet | Codzienny clear ma parytet `excluded` (nazwy) ↔ `excluded_cids` (cid), np. `['Bartek O.','Jakub OL','Jakub W']` ↔ `['123','370','492']`. |

---

## 4. FINDINGI (SPG-01..08)

| ID | Klasa | Waga | Opis | Zalecenie (dla właściciela — NIE wykonane, audyt read-only) |
|---|---|---|---|---|
| **SPG-01** | O | **wysoki** | `manual_overrides.save()` bez flock; 3 żywi pisarze (Telegram+konsola+reset) robią read-modify-write → lost-update wykluczeń. Atomic rename chroni tylko przed rozdarciem pliku. | `flock LOCK_EX` obejmujący cały read-modify-write (albo jedna kolejka zapisu). Ta sama klasa co `pending_proposals.json`. |
| **SPG-02** | B / J / A2 / F | **wysoki** | Konsola (`fleet_state._load_excluded`) czyta tylko `excluded` (nazwy); silnik (`courier_resolver`) blokuje po nazwie LUB `excluded_cids`. Fix 10.06 w 1 bliźniaku. Koordynator może widzieć „dostępnego" kuriera, którego Ziomek cicho pomija (i odwrotnie). | Konsola też konsumuje `excluded_cids` (najlepiej import `get_excluded_cids` z venv Ziomka — parytet przez wspólny kod, nie 2. kopię). |
| **SPG-03** | N / A1 / L | średni | Próg „czasówka=60" w ≥8 miejscach, 5 stałych + hardcode (§2.2). Dziś spójny (`>=`,60), ale zmiana = rozjazd. Kolizja magic-60: `CZASOWKA_MIN_PROPOSAL_SCORE=60` to punkty. | Jedno źródło `common.CZASOWKA_PREP_MIN` importowane wszędzie; zdjąć lokalne `CZASOWKA_THRESHOLD_MIN`/`CZASOWKA_PREP_MIN`; nazwać score-60 osobno. |
| **SPG-04** | M | średni | Fail-OPEN w ciszy: uszkodzony `manual_overrides.json` → `load()`→`{}` → wszystkie wykluczenia znikają; tylko `_log.warning`, brak alertu. | Przy fail-open na TYM pliku (sklep bezpieczeństwa) podnieś LOW-alert (wzór koord-cascade), nie sam warning. |
| **SPG-05** | K / L | niski | `auto_koord.is_unassigned`: `KOORDYNATOR_CID=26` zdefiniowany, ale guard używa literału `int(cid)==0` pod mylącym komentarzem „Already-Koordynator". Netto poprawne (każdy ≠0 = assigned, 26 też), ale przez przypadek. Pułapka dla przyszłej sesji. | Komentarz+kod spójne: `int(cid) not in (0,)` z jawnym „każdy przypisany" albo usuń mylący komentarz. |
| **SPG-06** | E | niski | `dispatch-koord-cascade.service` NIE ma OnFailure drop-in, mimo że docstring monitora zakłada „kto pilnuje strażnika → systemd OnFailure". Kontrast: `overrides-reset` ma onfailure+cron_health. Journal czysty (luka teoretyczna, ale klasa incydentu „silent cron 4 dni"). | Dopiąć `onfailure.conf` do koord-cascade (jak reszta 14 unitów). |
| **SPG-07** | E | niski | `auto_koord.emit_event_log`: race-avoided (poprawny idempotentny skip, `skipped=True`) logowany jako `AUTO_KOORD_FAILED` (8/8 „failów" to skipy). Metryka failów zanieczyszczona. | Trzeci event `AUTO_KOORD_SKIPPED` dla `skipped=True`. |
| **SPG-08** | H | niski | Brak per-wpis TTL (lifecycle na jednym cronie 06:00). `auto_koord_log.jsonl` rośnie bez rotacji (496 KB od 05.05). | (opc.) sanity „updated_at starszy niż X h" + rotacja logu. |

**Dodatkowo (minor A1 wewnątrz pliku):** `manual_overrides` ma 3 zachodzące na siebie funkcje merge nazw↔cid (`_load_names`, `_load_name_to_cid`, `_all_name_to_cid`) — częściowa duplikacja precedencji kurier_ids/courier_names/grafik_full_names.
**Latent (D/M, nieaktywny dziś):** klucze `EARLY_BIRD_THRESHOLD_MIN`/`ENABLE_WORKING_OVERRIDE` w flags.json są NIEOBECNE (nie null) → defaulty trzymają. Gdyby ktoś wpisał `null` (np. przez toggle UI): `_early_bird_threshold_min()`→`float(None)` crash pipeline; `C.flag(...,default=...)`→`bool(None)=False` cicho wyłącza working-override. `.get(k) or default` byłby odporny.

---

## POKRYCIE

**Lane:** 2 pliki rdzeniowe (`manual_overrides.py`, `auto_koord.py`) przeczytane w 100%; wszyscy konsumenci silnikowi + konsolowi + panel_watcher hook + monitor + skrypt resetu + 4 unity systemd przeczytane; oracle na żywo (store-kopia, 2× journal 14d, auto_koord_log 1065 wpisów, shadow ledger 5 dni).

Mapa na 15 klas taksonomii (`ZIOMEK_COHERENCE_AUDIT_DESIGN.md §1`):

| Klasa | Status | Ustalenie |
|---|---|---|
| **A** (jedno źródło / N-kopii) | ✅ | A1: próg-60 sprawl (SPG-03) + 3 merge-fn w mo. A2: pojęcie „wykluczony" silnik↔konsola (SPG-02). |
| **B** (asymetria bliźniaków) | ✅ | `excluded_cids` egzekwowane w silniku, nieczytane w konsoli (SPG-02). |
| **C** (naruszenie warstw) | ✅ | early_bird short-circuit PRZED pulą (C2-adjacent) — ŚWIADOMY, instrumentowany (EARLYBIRD-01). Brak nowego naruszenia w tych 2 plikach. |
| **D** (dryf realności flag) | ✅ | Docstring auto_koord „default False" vs live True. Flagi None=absent (OK) + latent null-crash. auto_koord poza ETAP4 (single-proc panel_watcher — akceptowalne). |
| **E** (kłamiące/void przyrządy) | ✅ | koord-cascade monitor WALIDNY (0 na 01.07 zgodne z always-propose ON), ale bez OnFailure (SPG-06). auto_koord FAILED mislabel (SPG-07). |
| **F** (dryf semantyki pól) | ✅ | `excluded`(nazwy)/`excluded_cids`(cid) — 2 pola 1 pojęcia, pisane razem, konsumowane asymetrycznie (⊂ SPG-02). |
| **G** (kalibracja na złej osi) | ➖ N/A | Brak kalibracji/korekty w tych plikach (out-of-scope lane). |
| **H** (cykl życia/janitorial) | ✅ | Brak per-wpis TTL, lifecycle=1 cron; log bez rotacji (SPG-08). |
| **I** (konflikt reguł) | ✅ | auto_koord↔czasowka_scheduler komplementarne; always-propose↔KOORD-gate monitorowane; early_bird-first precedencja zdefiniowana. Brak żywego konfliktu. |
| **J** (cross-repo/multi-proces) | ✅ | Wykluczenie dispatch_v2↔panel: ZAPIS przez subprocess do kanonu (dobre), ODCZYT re-implementowany w fleet_state i rozjechany (SPG-02). |
| **K** (martwy/szczątkowy kod) | ✅ | `KOORDYNATOR_CID` nieużyty w guardzie + stale docstring (SPG-05). |
| **L** (słownictwo/jednostki/TZ) | ✅ | magic-60 przeciążony (minuty prep vs minutes_ahead vs punkty score) (SPG-03). |
| **M** (cicha awaria/sentinele) | ✅ | Fail-open cichy na sklepie bezpieczeństwa (SPG-04) + latent null-flag. |
| **N** (rozsyp progów) | ✅ | Wartość-60 w 8 miejscach/5 stałych (SPG-03). |
| **O** (współbieżność/wyścig) | ✅ | 3 pisarzy bez locka (SPG-01); dedup triggera auto_koord OK; race-guard perform_auto_koord OK. |
| CROSS-CUTTING (test-suite jako oracle) | 🟡 PARTIAL | Istnieją `tests/test_auto_koord.py`, `tests/test_v326_hotfix_parser.py` — NIE uruchomione (read-only, brak wykonania pytest). |

**14/15 klas sprawdzonych z findingiem lub potwierdzeniem** (G=N/A dla lane), cross-cutting test-suite = PARTIAL.

---

## JAWNE LUKI

1. **Nie uruchomiono testów** — READ-ONLY, brak wykonania `pytest tests/test_auto_koord.py tests/test_v326_hotfix_parser.py`. Nie wiem czy zielone na bieżącym kodzie (choć oba pliki testowe istnieją i pokrywają parser + is_czasowka).
2. **Autoryzacja `/stop` w Telegramie niezweryfikowana** — przeczytałem `telegram_approver.py` tylko w miejscach wywołania `parse_command` (2570/2652/3011); NIE prześledziłem czy jest gate uprawnień (kto z grupy może wykluczać kuriera). Możliwy brak kontroli dostępu do bramki puli — do sprawdzenia osobno.
3. **Druga interpretacja `excluded` w konsoli** — sprawdziłem `fleet_state._load_excluded/_load_working` + `blocked@884`; `coordinator.py:347` („ustaw stan przypisywania … LIVE") tylko z grepa, nie prześledzony pełny flow. Może istnieć trzecia ścieżka odczytu.
4. **Lost-update (SPG-01) NIEZMIERZONY** — brak audit-trail zapisów z sub-sekundowymi timestampami; ryzyko teoretyczne, bez dowodu wystąpienia w logach.
5. **`gastro_assign.py --koordynator` poza audytem** — nie zweryfikowałem że subprocess faktycznie ustawia `id_kurier=26` (potwierdzenie pośrednie: 1057 ok + brak re-eskalacji). Sama bramka gastro poza lane.
6. **EARLYBIRD-01 forward-shadow (E)** — nie sprawdziłem czy `earlybird_shadow.jsonl` się zapełnia (flaga OFF default); walidacja tego przyrządu = osobna sonda.
7. **Realna częstotliwość równoczesności konsola↔Telegram** — nieoszacowana (ilu koordynatorów pisze jednocześnie z Adrianem). Determinuje realną wagę SPG-01.

---
*Koniec sweep. Wszystkie ustalenia read-only; żadna zmiana produkcji/flag/systemctl nie wykonana. Findingi SPG-01..08 = rekomendacje do osobnych sprintów wg PRZYKAZANIA #0 (protokół + ACK), nie akcje tego audytu.*
