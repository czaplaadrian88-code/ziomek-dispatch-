# C15 — ORACLE: carried_first_guard (strażnik #1) — lane C (runtime-oracle)

**Agent:** C15-carried-first-guard · **Tryb:** READ-ONLY (zero edycji/restartów/flipów; tool tylko `--dry` write=False) · **Data:** 2026-06-30 ~17:49 UTC · **Sesja:** tmux 2.
**Werdykt instrumentu: 🔴 VOID** — strażnik NIE liczy „identycznie jak silnik". Działa pod ODWROTNĄ konfiguracją flag niż silnik → 87% rekordów = fikcyjne `no_position`, a detektor carried-first produkuje fałszywe trafienia względem okrojonego (de-clawed) kanonu i przegapiłby prawdziwy nawrót w silniku.

---

## 0. CO TO ZA PRZYRZĄD (ground facts, świeży grep)
- Plik: `tools/carried_first_guard.py` (mtime 29.06 23:17, 182 linie). Timer `dispatch-carried-first-guard.timer` **3 min LIVE** (ostatni run 17:46:42, next 17:49:42).
- Output: `dispatch_state/carried_first_guard.jsonl` **mtime 30.06 17:49:46 FRESH**, 177 KB, **1177 rekordów (wszystkie z dziś, od 07:08:23)**. Plik puchnie (seed mówił 81 KB/13:11 → teraz 177 KB).
- Cel (docstring l.4-11 + MEMORY): „carried-first naprawiane 11× wracał, bo nie było DETEKTORA… raz na uruchomienie klasyfikuje KAŻDEGO aktywnego wielozleceniowego kuriera do REŻIMU… jak wróci, to ALERT w minutę". Wybór Adriana 29.06 „zamknąć carried-first żeby nie wracał 12 raz". **ZERO wpływu na decyzje** (pisze tylko jsonl+log).
- Rdzeń logiki: dla każdego kuriera z ≥2 aktywnymi zleceniami liczy `anchor = PR._start_anchor(...)` i `canon = PR._apply_canon_order_invariants(stops, ...)`, po czym klasyfikuje reżim: `no_position` (anchor None) / `no_plan` / `plan_invalidated` / `coverage_gap` / `carried_first` / `canon_divergence` / `ok`. RISK_KINDS = {no_position, no_plan, plan_invalidated, coverage_gap} ∪ {carried_first}.

## 1. METODA ORACLE (druga, niezależna metoda — C9/C11)
1. **A/B-replay tej SAMEJ reużytej funkcji silnika pod DWIEMA konfiguracjami env**, na identycznym żywym stanie, 2× każda (determinizm). Tool odpalony `--dry` (write=False → NIE pisze do `dispatch_state`).
   - **CONFIG A** = `env -i` (PUSTE env — DOKŁADNIE jak biegnie usługa strażnika).
   - **CONFIG B** = 14 flag env z `dispatch-plan-recheck.service` (`ENABLE_GPS_FREE_ANCHOR=1 … ENABLE_PLAN_CANON_ORDER_INVARIANTS=1` …).
2. **Niezależna re-derywacja smell carried-first** (własna impl, bez importu toola) na żywym rekordzie cid=123 + inwarianty (ten sam multiset stopów, brak fikcyjnych pickupów, struktura).
3. **Join `no_position` cids ↔ `courier_last_pos.json`** (czy silnik miałby pozycję mimo braku GPS).
4. Lektura źródła flag (`os.environ.get` at-import) + `systemctl show -p Environment` per usługa + `flags.json`.

## 2. WYNIK A/B (deterministyczny — to jest dowód) 🔴
| Config | jak biegnie | wynik na 5 wielozlec. kurierach (ten sam stan, 17:49) | risk |
|---|---|---|---|
| **A — PUSTE env** | **realna usługa strażnika** | `no_position=5` | **5/5 = 100%** |
| **B — env silnika (14 flag=1)** | to, co LICZY silnik | `ok=2, canon_divergence=3` | **0/5 = 0%** |

A1==A2, B1==B2 (deterministyczne). **Ten sam stan, ten sam moment, ta sama reużyta funkcja `_start_anchor`/`_apply_canon_order_invariants` — przeciwny wynik.** Strażnik krzyczy „5/5 ryzyko", silnik (jego prawdziwe flagi) widzi „0/5 ryzyka, 2 OK + 3 łagodny dryf".

## 3. KORZEŃ — DRYF ENV/FLAG MIĘDZY PROCESAMI (źródło)
**Wszystkie kotwico/kanon-owe flagi to module-level stałe czytane z `os.environ` AT IMPORT** (świeży grep `plan_recheck.py`):
```
347: ENABLE_GPS_FREE_ANCHOR            = os.environ.get("ENABLE_GPS_FREE_ANCHOR","0")=="1"
354: ENABLE_GPS_FREE_ANCHOR_LAST_POS   = os.environ.get(...,"0")=="1"
368: ENABLE_PLAN_CANON_ORDER_INVARIANTS= os.environ.get(...,"0")=="1"
377: ENABLE_NO_RETURN_TO_DEPARTED_PICKUP=os.environ.get(...,"0")=="1"
425: ENABLE_CARRIED_FIRST_RELAX        = os.environ.get(...,"0")=="1"
475: ENABLE_RELAX_COLOC_PICKUP         = os.environ.get(...,"0")=="1"
488: ENABLE_NONCARRIED_DROPOFF_REORDER = os.environ.get(...,"0")=="1"
363: ENABLE_PLAN_SEQUENCE_LOCK         = os.environ.get(...,"0")=="1"
```
`systemctl show -p Environment`:
- `dispatch-carried-first-guard.service` → **`Environment=` (PUSTE)**. Brak drop-in dir (`...service.d/` nie istnieje), brak `EnvironmentFile`.
- `dispatch-plan-recheck.service` → 14 flag `=1` (m.in. wszystkie powyższe).
- `dispatch-panel-watcher.service` → te same 14 `=1`.

`flags.json`: **WSZYSTKIE 8 flag ABSENT** (env-only drop-iny) → brak jednego źródła. Każdy importer `plan_recheck`, który nie zreplikuje 14-zmiennego env, po cichu dostaje default-OFF.

➡ **Strażnik importuje `plan_recheck` z PUSTYM env → reużyte funkcje silnika lecą z KAŻDĄ flagą OFF, dokładnie ODWROTNIE niż silnik.** Docstring l.5-6 „liczy IDENTYCZNIE jak silnik (… żaden drugi algorytm)" = **strukturalnie FAŁSZYWY w deploymencie** (ta sama funkcja, przeciwne flagi = inny algorytm efektywny).

## 4. MECHANIZM #1 — FIKCYJNE `no_position` (87% rekordów)
`_start_anchor` (l.554-594): z `ENABLE_GPS_FREE_ANCHOR=0` (l.570-571) → `return ((gps),None,"gps_pwa") if has_gps else None`. Brak żywego wiersza GPS → **None → `no_position` (risk=True)**. Z flagą ON silnik spada przez `_last_event_anchor` → `_earliest_committed_pickup_anchor` → `gps_stale` → `_last_known_pos_anchor` (l.586-593, `ENABLE_GPS_FREE_ANCHOR_LAST_POS=1`) → REALNA kotwica → NIE `no_position`.

**Join dowodowy (świeży `courier_last_pos.json`):** 11 distinct cids `no_position` dziś = `{179,207,370,376,413,441,447,484,515,531,536}` — **WSZYSTKIE 11 mają `last_known_pos_store=True`, `live_gps_row=False`.** Silnik (LAST_POS ON, feature `ENABLE_COURIER_LAST_KNOWN_POS` LIVE od 08.06) zakotwiczyłby każdego → `last_known_pos`, nie `no_position`. Strażnik (LAST_POS OFF) ich gubi.

**Rozkład dziś:** `no_position=1025` (87,1%), `ok=111`, `plan_invalidated=32`, `canon_divergence=8`, `carried_first=1`. **risk=True: 1058/1177 = 89,9%.** A/B potwierdza: pod env silnika te same kurierki → 0 `no_position`. **→ ~90% sygnału ryzyka strażnika to artefakt env, nie realne ryzyko carried-first.**

## 5. MECHANIZM #2 — CARRIED-FIRST FALSE-POSITIVE vs OKROJONY KANON
`_apply_canon_order_invariants` (l.1478-1557) — co biegnie pod PUSTYM env strażnika:
| Krok | linia | gating | guard (env pusty) | silnik (env=1) |
|---|---|---|---|---|
| carried picked_up → front | 1487-1494 | bezwarunkowo | ✅ ON | ✅ ON |
| sort odbiorów po committed | 1495-1511 | bezwarunkowo | ✅ ON | ✅ ON |
| `_coalesce_same_pickup_nodes` (no-return) | 1519 | `ENABLE_NO_RETURN_TO_DEPARTED_PICKUP` | ❌ OFF (tylko detekcja+warn) | ✅ ON |
| **`_relax_carried_first`** | 1524 | `ENABLE_CARRIED_FIRST_RELAX` | ❌ **OFF** | ✅ ON |
| `_lex_committed_window_reorder` | 1538 | wewn. gate l.1316 (SHADOW/APPLY) | ❌ no-op (oba OFF) | ✅ ON |
| `_reorder_noncarried_min_drive` | 1547 | `ENABLE_NONCARRIED_DROPOFF_REORDER` | ❌ OFF | ✅ ON |

Zapisany plan (`courier_plans.json`) został wyprodukowany przez silnik **Z** relax/no-return/noncarried/lex ON. Strażnik rekomputuje kanon **BEZ** nich → kanon się różni od zapisu → smell strzela. **Strażnik flaguje WŁASNE okrojenie jako naruszenie.**

**Niezależna re-derywacja (rekord cid=123, 12:17, jedyny carried_first dziś):**
- `saved=[464d,470p,473p,470d,462p,462d,473d]`, `canon=[464d,462p,470p,470d,473p,462d,473d]`.
- Inwariant ten-sam-multiset stopów: **PASS** (7==7). Struktura (pickup nie po własnym dropoffie): **PASS** oba. Zero fikcyjnych pickupów: **PASS**.
- Mój smell: **fires at i=3** — saved dowozi 484470 (idx3) i DOPIERO potem odbiera 484462 (idx4); kanon-strażnika odbiera 484462 (idx1) PRZED dowiezieniem 484470 (idx3). **Algorytm smell jest wewnętrznie poprawny.**
- ALE: kanon-strażnika powstał z `CARRIED_FIRST_RELAX=0`. Silnik z relax ON dał właśnie `saved` (to JEST wyjście silnika). → `carried_first` = **najpewniej FALSE POSITIVE** względem prawdziwego kanonu silnika. A/B @17:49 z env silnika: `carried_first=0`.

**Konsekwencja dla celu instrumentu:** strażnik nie potrafi orzec „silnik-z-pozycją by tak NIE ułożył" (jego teza, l.43-44), bo NIE uruchamia ani logiki pozycji silnika, ani kanonu silnika. Prawdziwy nawrót carried-first w silniku (relax ON produkujący zły porządek) byłby liczony względem innego kanonu → **mógłby zostać przegapiony LUB utonąć w 1025 fikcyjnych `no_position`.** Obietnica „12. nawrót = wpis w minutę" jest **niespełniona** w obu kierunkach (fałszywy alarm + ślepota na prawdę).

> Uwaga: `canon_divergence` (8 dziś, cids 123/492) NIE jest w RISK_KINDS i utrzymuje się też pod env silnika (A/B: 3 łagodne). To „plan ≠ kanon-teraz, nie carried-first" — w większości benign (plan zrobiony wcześniej, stan się ruszył). NIE to jest defektem; defektem jest fikcyjne `no_position` + carried_first vs okrojony kanon. `plan_invalidated=32` = jedyny w pełni wiarygodny reżim (czyta tylko pole `plan.invalidated_at`, niezależne od flag).

## 6. INWARIANTY-TRIPWIRE (wynik)
| Inwariant | wynik |
|---|---|
| ten sam zbiór+liczba stopów (saved vs canon) | ✅ PASS (7==7 na rekordzie cf) |
| ZERO fikcyjnych pickupów odebranych | ✅ PASS (strażnik z definicji nie fabrykuje; l.14-15) |
| determinizm (≥2 odpalenia) | ✅ PASS (A1==A2, B1==B2) |
| **FIDELITY: strażnik ≡ silnik** | 🔴 **FAIL** — risk 5/5 (guard) vs 0/5 (silnik) na identycznym stanie |

## 7. INSTANCJE (plik:linia świeży · źródło/objaw · łatane? · otwarte? · severity · dedup)
| ID | klasa | plik:linia | kind | opis | sev | open |
|---|---|---|---|---|---|---|
| C15-F1 | D+J+E | `tools/carried_first_guard.py:5` (claim) + `plan_recheck.py:347` (os.environ at import) + unit `Environment=∅` | source | Strażnik biegnie z pustym env → reużyte funkcje silnika lecą z 14 flagami default-OFF (silnik ON). „Liczy identycznie jak silnik" = fałsz. | P1 | tak |
| C15-F2 | E+M | `tools/carried_first_guard.py:100-101` + `plan_recheck.py:570-594` | symptom | `no_position`=1025/1177 (87%) fikcyjne — 11/11 cids mają last-known-pos store; silnik (LAST_POS=1) kotwiczy je → nie no_position. 90% „ryzyka" to artefakt. | P1 | tak |
| C15-F3 | E+C | `tools/carried_first_guard.py:121` + `plan_recheck.py:1524` | symptom | `carried_first`(1)+`canon_divergence` liczone vs kanon BEZ relax/no-return/noncarried/lex (silnik je ma ON) → false-positive; detektor nie certyfikuje „silnik by tak nie ułożył" i przegapiłby prawdziwy nawrót. Smell-algorytm sam w sobie poprawny. | P2 | tak |

**Wszystkie: is_patched=false, still_open=true.** Read-only instrument → ZERO bezpośredniego wpływu na decyzje dispatchu (nie P0 produkcyjny), ale jako STRAŻNIK-PRAWDA = VOID (cel sprintu „zamknąć carried-first" niespełniony).

## 8. DEDUP / ROOT
- C15-F1/F4 → **K1 „brak jednego źródła"** (flagi env-only, czytane `os.environ` at-import → N procesów = N konfiguracji). Bliźniaczo do A6 frozen `_lex_qual` shadow (instrument reużywa kod silnika, ale pod inną/zamrożoną konfiguracją). NOWA instancja klasy: **„reused-engine-instrument runs under default-OFF env"** — kandydat do tej samej naprawy co „one selection key/route module" (wspólne źródło konfiguracji, nie 2 środowiska).
- C15-F2/F3 → objawy F1 (manifestacja w jsonl). NIE liczyć jako osobne korzenie — zwijają się do C15-F1.
- Most do K5 (sentinele): `no_position` strażnika = sentinel „brak pozycji" rozjeżdżający się z realną pozycją konsumentów/silnika (last-known-pos) — analogia do `_SYNTH_POS`/`BIALYSTOK_CENTER` z grupy 3b/cross-cutting A6.
- 12. „kłamiący przyrząd" do listy z audytu anty-kłamstwo 28.06 (tam 11 znaleziono; ten NIE był w tamtej puli — read-only guard powstał 29.06).

## 9. CO BY TO NAPRAWIŁO (tylko DIAGNOZA — STOP przed naprawą, DoD)
Naprawa U ŹRÓDŁA = strażnik MUSI biec pod TĄ SAMĄ konfiguracją flag co silnik. Opcje (do decyzji w sprincie naprawczym, NIE teraz):
(a) dodać do unitu `dispatch-carried-first-guard.service` te same drop-iny env co `dispatch-plan-recheck` (najprostsze, ale utrwala „N kopii env"); lub
(b) źródłowo: `plan_recheck` czyta te flagi z JEDNEGO źródła współdzielonego (flags.json/common), nie z `os.environ` at-import — wtedy każdy importer (strażnik, testy, narzędzia) widzi tę samą prawdę = leczy całą klasę, nie tylko strażnika (zgodne z „fix u źródła" + K1).
Po naprawie: re-run A/B musi dać guard≡silnik (risk strażnika == risk silnika na tym samym stanie) + golden-test parytetu env importera.

---

## 10. POKRYCIE (jawne — C11-c)
**Zbadane (świeży grep/odczyt/run dziś):**
- `tools/carried_first_guard.py` cały (1-182): evaluate, klasyfikacja reżimów, `_carried_first_smell`, `_load_plans`, write-path, main.
- `plan_recheck.py`: `_start_anchor` (554-594), `_earliest_committed_pickup_anchor` (534-551), `_last_known_pos_anchor` (597-609), `_apply_canon_order_invariants` (1478-1557), `_lex_committed_window_reorder` gate (1308-1317), `ACTIVE_STATUSES` (114), definicje 8 flag (346-488), `_retime_one_bag_plan` kontekst (1560-1592).
- ENV: `systemctl show -p Environment` dla guard / plan-recheck / panel-watcher / shadow; brak drop-in dir guarda; `flags.json` (8 flag ABSENT).
- DANE: `carried_first_guard.jsonl` (1177 rek. dziś), `courier_last_pos.json` (join 13 cids), live `_load_gps_positions()`.
- ORACLE: tool `--dry` 2×A + 2×B (deterministyczny); niezależny smell + inwarianty na rekordzie cf.

**NIE zbadane (luki + powód):**
1. **Dokładna reprodukcja stanu 12:17 cid=123 pod env silnika** — stan się przesunął; oparłem werdykt na logice de-claw + A/B @17:49 (carried_first 0 pod env silnika). Pewność: wysoka, ale nie bit-dokładne odtworzenie historycznego ticku.
2. **Ciała `_relax_carried_first`/`_coalesce_same_pickup_nodes`/`_reorder_noncarried_min_drive`** — nie czytane linia-po-linii; de-claw wywnioskowany z gating-ifów (1519/1524/1547) i potwierdzony deltą A/B. Wystarczające do werdyktu.
3. **Realny fallback konsoli/apki w `no_position`** (czy faktycznie wchodzą w carried-first fallback) — nie sprawdzone runtime (poza dispatchem; granica STOP na dyspozytorni). Teza docstringu o „konsumentach spadających w fallback" nie zweryfikowana po stronie konsumentów.
4. **Write-path `_append_jsonl`-podobny (l.141-152)** — non-atomic (tmp→read→append→remove), bez swallow; A4 sygnalizował klasę M „cicha awaria zapisu" — NIE potwierdziłem utraty rekordów (deprioryt; plik puchnie normalnie 1177 rek.).
5. **Czy inne narzędzia importujące `plan_recheck` mają ten sam dryf** (np. `pickup_slip_monitor`, `ziomek_pred_calibration`) — poza zakresem C15; sygnalizuję jako klasę do sweepu (każdy importer plan_recheck pod własnym env = ten sam K1).
