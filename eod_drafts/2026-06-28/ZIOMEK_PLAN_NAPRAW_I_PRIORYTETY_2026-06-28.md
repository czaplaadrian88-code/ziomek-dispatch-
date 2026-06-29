# Ziomek — priorytety, plan naprawy i ulepszeń (prostym polskim)
**Data:** 2026-06-28 · **Kontekst:** Ziomek = moat firmy; w tym roku skalowanie przez **franczyzę na inne miasta**.
**Skąd to:** z nocnego, dogłębnego audytu (18 obszarów, 86 agentów, read-only) + sesji 28.06. Pełne źródła: `eod_drafts/2026-06-27/ZIOMEK_DEEP_AUDIT_REPORT.md`, `…_FINDINGS.json`, `…_AUDIT_PROTOCOL_ENRICHMENT.md`. Reguły pracy: `memory/ziomek-change-protocol.md` (Przykazanie #0, Załączniki A/B/C).

---

## 0. W jednym zdaniu
Ziomek jest **zdrowy w fundamencie** (zero błędów krytycznych P0; twarde reguły — kolejność „najpierw bezpieczeństwo, potem optymalizacja", limit 35/40 min na jedzenie, „zawsze coś zaproponuj" — trzymają). Problemy to **średni i niski dług** — ale część z nich, **jeśli pojechalibyśmy z nią na 10 miast, zaboli 10×**. Dlatego plan jest ułożony nie „od najgłośniejszego buga", tylko **„co musi być solidne, żeby franczyza nie rozjechała się o własne fundamenty".**

---

## 1. Co znaczy „dobry Ziomek" dla franczyzy (5 filarów moatu)
To jest soczewka, przez którą ustawiłem priorytety. Każda naprawa służy któremuś z tych filarów.

1. **Te same decyzje na każdym ekranie.** Silnik, konsola koordynatora i apka kuriera muszą pokazywać DOKŁADNIE to samo. W Białymstoku rozjazd wyłapie Adrian. W nowym mieście franczyzobiorca i kurier nie mają Adriana — rozjazd = utrata zaufania do systemu w 1. tygodniu.
2. **Widać, co Ziomek robi i czy robi dobrze.** Franczyzobiorca musi mieć dashboard „czemu tak zdecydował / czy dowozimy na czas", liczony z **zapisanych danych**, nie z logów, które czyta tylko Adrian.
3. **Nowe miasto = konfiguracja, nie przepisywanie kodu.** Dziś Białystok siedzi w kodzie (dzielnice, korki, współrzędne, godziny szczytu, lista kurierów). Każde nowe miasto przez edycję kodu = nie skaluje się i łamie zasadę „jedna baza kodu dla wszystkich franczyz". **To największy blocker franczyzy.**
4. **Zmiana jest bezpieczna.** Przy 10 miastach jedna zmiana dotyka wszystkich naraz. Testy i flagi muszą łapać regresję, zanim trafi na produkcję — inaczej jeden błąd psuje 10 miast jednocześnie.
5. **Ziomek sam się kalibruje pod miasto.** Korki, „klasy" kurierów, biasy czasów muszą uczyć się z danych danego miasta, nie być ręcznie strojone przez Adriana (to nie skaluje się do 10 miast).

---

## 2. Co znalazł audyt — prostym polskim (pogrupowane)
Liczby: **81 znalezisk — 0 krytycznych, 0 wysokich (oba „wysokie" po weryfikacji zeszły do średnich), 31 średnich, 48 niskich.** 8 „oczywistych bugów" upadło po sprawdzeniu (dobrze — nie gonimy duchów).

### A. Bugi żywe, które dziś działają na produkcji
- **Apka pokazuje kurierowi inną kolejność niż konsola/silnik** (faseta #3, `cap-carried-relax-app-console-divergence`). Apka każe „najpierw dowieź to, co wieziesz", a silnik wcześniej policzył „odbierz po drodze, potem dowieź". 44–75 worków dziennie. **Już naprawiane przez sesję 17** (przez protokół, deploy za ACK). Dla franczyzy to filar #1.
- **Równe traktowanie kurierów bez GPS jest po cichu cofane dla 20% zleceń** (`twin-pln-pure-resort-stale-bucket`). Jedna gałąź selekcji używa starej, nierównej reguły sprzed decyzji „traktuj równo". Efekt: czasem proponuje innego kuriera niż powinien.
- **Przy awarii map (OSRM) czasy dojazdu puchną ~1,5× w szczycie** (`osrm-fallback-double-traffic`). Dwa razy doliczany korek. Sam w sobie rzadki, ale uderza dokładnie wtedy, gdy system już ma gorszy dzień → fałszywe „spóźnienia" → niepotrzebne ręczne interwencje.
- **„Grupowanie z tej samej restauracji" wstawia odbiór dwa razy** w trybie awaryjnym planera (`rst-grouping-greedy-double-pickup`). Dziś schowane, bo działa lepszy planer (OR-Tools). ALE: gdyby ktoś w panice wyłączył OR-Tools (jest taki 1-przyciskowy rollback), błąd wychodzi szeroko. **Reguła: wyłączasz OR-Tools → wyłącz też grupowanie.**

### B. „Ślepota" — rzeczy, których Ziomek nie zapisuje, więc nie widać, czy działają
- **Część metryk jest liczona, ale nie zapisywana** do dziennika decyzji (`metser-*`, wzorzec #16). Dotyczy m.in. „o ile przekroczył limit po zmianie", „czy zadziałał twardy limit na tier", „skąd wziął czas odbioru". Skutek podwójny: (1) nie zrobisz dashboardu dla franczyzy bez tych danych; (2) **nie udowodnisz, że zmiana pomogła, zanim ją włączysz** — a to wymóg naszego protokołu. Dziennik jest dziś przechylony: rzeczy „miękkie" się zapisują, „twarde" giną.

### C. Miny, które wybuchają przy włączaniu funkcji (ważne, bo każde miasto będzie włączać)
- **Plik z propozycjami (`pending_proposals.json`) ma 3 piszące procesy bez „zamka"** (`dsi-pending-multiwriter`). Dziś bezpieczne TYLKO dlatego, że Telegram jest wyciszony. Włączenie Telegrama (a każde miasto go włączy) zmienia bezpieczeństwo danych bez żadnej zmiany w kodzie.
- **Mechanizm „odłóż na później" (postpone) jest po cichu zepsuty** (`czas-postpone-*`, `dsi-postpone-…`). Czyta zły kształt danych (`cid` zamiast `courier_id`). Śpi, bo jest wyłączony — ożyje jako bug w dniu, w którym ktoś go włączy.
- **Po zmianie zmiany kurierom dochodzi kara za pracę po godzinach** (`pipe-postshift-gate-exclusion`), ale ta kara nie jest dopisana do listy wyjątków bramki „zaproponuj zamiast milczeć". Przed jej włączeniem trzeba to dopisać, inaczej system zacznie częściej „milczeć" zamiast proponować.

### D. „Jedno źródło prawdy", które jest kłamstwem (dług architektury)
- **Kolejność trasy żyje w 3–4 osobnych kopiach** (silnik / konsola / apka / martwy panelsync), mimo że komentarz mówi „wszyscy delegują tutaj" (`cap-console-reimpl`, `pr-route-podjazdy-not-shared`, reguła C7). To dokładnie dlatego faseta #3 się rozjechała: poprawkę dodano do jednej kopii, a nie do drugiej. Docelowo: **zlać do jednej funkcji.**
- **Stare, „nierówne" kopie reguły kubełkowania** w kilku miejscach selekcji (`twin-*-stale-bucket`) — dziś w większości nieaktywne, ale czekają, żeby kolejna zmiana je po cichu rozjechała.

### E. Higiena, która decyduje, czy zmiana jest bezpieczna
- **~24 żywe flagi nie są w rejestrze `ETAP4_DECISION_FLAGS`** (`tests-etap4-registry-drift`). Skutek: testy, które miały sprawdzać zachowanie „po staremu", po cichu biegną „po nowemu" → testy kłamią. Przy 10 miastach to znaczy „regresja przechodzi przez testy niezauważona".
- **Dwie żywe flagi sterujące TWARDYM limitem 35 min nie mają testu „włączona ≠ wyłączona"** (`feas-r6-bagcap-untested`, `tests-plan-recheck-tier-dwell`). To najtwardsza reguła systemu — i nie jest zabezpieczona testem.

### F. Drobne sprzątanie (tanie, podnosi jakość)
- 268 plików `.bak` w repo, martwe moduły, martwe flagi (`A4_TEST_FLAG`), wygasłe timery, mylące docstringi, TTL liczony od złego momentu (`crg-lastpos-ttl`). Pojedynczo błahe, razem — szum, w którym łatwo o pomyłkę.

### Czego audyt NIE sprawdził, a dla franczyzy jest kluczowe
- **Pełnej „gotowości na wiele miast" (multi-city / multi-tenant).** Audyt szukał bugów i długu, nie robił dedykowanego przeglądu „co jest zahardkodowane na Białystok". Widoczne przykłady do wyniesienia do konfiguracji per-miasto: środek miasta (`BIALYSTOK_CENTER`), tablica korków (`V326_OSRM_TRAFFIC_TABLE`), dzielnice/ulice (`districts_data.py`), współrzędne awaryjne firmowego konta, okna szczytu/blokad, lista i wykluczenia kurierów, mapowania restauracji. **To wymaga własnego, osobnego audytu — i to jest największa praca pod franczyzę.**

---

## 3. Priorytety (operacyjnie + pod franczyzę)
Kolumna „Pilność operacyjna" = jak bardzo boli DZIŚ (1 miasto). „Waga dla franczyzy" = jak bardzo zaboli przy skalowaniu. Sortuję po wadze dla franczyzy.

| # | Temat | Pilność dziś | Waga franczyza | Wysiłek | Status |
|---|---|---|---|---|---|
| **F1** | Wynieść Białystok z kodu do konfiguracji per-miasto | niska | **KRYTYCZNA** | duży (sprinty) | ✅ **audyt ZROBIONY 28.06 → proces-per-miasto** |
| **F2** | Dokończyć zapis metryk → dashboard „co robi/czy dobrze" | średnia | **wysoka** | średni | do zrobienia |
| **F3** | Higiena testów/flag (24 flagi do rejestru + testy twardych flag) | średnia | **wysoka** | mały-średni | do zrobienia |
| **B1** | Rozjazd konsola↔apka (faseta #3) | wysoka | wysoka | — | **w toku (sesja 17)** |
| **B2** | Równe traktowanie cofnięte dla 20% (PLN stary kubełek) | średnia | wysoka | mały | do zrobienia |
| **B3** | OSRM fallback podwaja korek | średnia | średnia | mały | do zrobienia |
| **B4** | Grupowanie double-pickup + sprzężenie flag | niska (uśpione) | średnia | mały | do zrobienia |
| **R3** | Sprint O2 (świeżość jedzenia) z twardym cap-Z, tier-aware | średnia | wysoka | średni-duży | **brama 02.07** |
| **R1** | `pending_proposals` bez zamka (przed re-enable Telegrama) | niska (uśpione) | wysoka | średni | przed włączeniem |
| **R2** | Postpone zepsuty (przed re-enable) | niska (uśpione) | średnia | mały | przed włączeniem |
| **R4** | post_shift: zapis metryki + wyjątek bramki (przed flipem) | niska | średnia | mały | przed flipem |
| **D1** | Zlać „jedno źródło kolejności" (3-4 kopie → 1) | niska | wysoka | średni | po B1 |
| **D2** | Sprzątnąć stare kubełki-bliźniaki | niska | średnia | mały | dług |
| **D3** | .bak / martwy kod / martwe flagi / timery | niska | niska | mały | dług |
| **META** | Przykazanie #0 wzmocnione (Załącznik B+C) | — | wysoka | — | **✅ zrobione 28.06** |

---

## 4. Plan naprawy — kolejność i dlaczego taka
Zasada nadrzędna: **wszystko idzie przez Przykazanie #0** (ETAP 0→7: stan na żywo → fix u źródła → mapa kompletności bliźniaków → dowód „włączona ≠ wyłączona" + zapis metryki + pełna regresja → dowód że POMAGA na danych → deploy za ACK → rollback gotowy). Bo to jest moat: jakość > szybkość, zero zmian „w połowie".

### Faza 0 — domknąć to, co w biegu (dni)
- **B1 (faseta #3)** — sesja 17 kończy przez protokół; deploy courier-api + panel **za Twoim ACK, poza szczytem**. Po deployu: monitor `q3_route_mismatches → 0` = dowód. (Ja zostaję read-only, nie wchodzę im w drogę.)

### Faza 1 — FUNDAMENT BEZPIECZEŃSTWA ZMIAN (najpierw, bo chroni wszystko dalej) (dni)
- **F3 higiena testów/flag** — wpisać ~24 żywe flagi do `ETAP4_DECISION_FLAGS`; dopisać testy „włączona ≠ wyłączona" dla twardych flag limitu 35 min. *Czemu pierwsze: bez tego każda kolejna naprawa może po cichu przejść regresją — a przy franczyzie to regresja w każdym mieście naraz.* Tani, ogromny zwrot.

### Faza 2 — WIDOCZNOŚĆ (bo bez niej nie udowodnisz reszty) (dni)
- **F2 zapis metryk** — dopisać brakujące metryki HARD do dziennika decyzji (serializer A+B / prefiksy). To odblokowuje: (a) dowody „pomaga" dla każdej kolejnej zmiany, (b) **dashboard dla franczyzobiorcy** „dowozimy na czas / czemu tak / które reguły się odpalają". To zaczyn produktu „panel franczyzy".

### Faza 3 — ŻYWE BUGI (równolegle z Fazą 2) (dni)
- **B2** równe traktowanie (PLN) → przepiąć na wspólny kubełek. **B3** OSRM fallback (nie liczyć korka dwa razy). **B4** grupowanie + zasada sprzężenia flag. Każdy mały, każdy z dowodem na danych.

### Faza 4 — ŚWIEŻOŚĆ JEDZENIA / O2 (brama 02.07) (sprint)
- **R3** — gotowy fix silnika `ENABLE_O2_READY_ANCHOR_SWEEP` jest ZBUDOWANY za flagą OFF; na 02.07 decyzja na danych tygodnia, z **twardym cap-Z** (limit świeżości) i **tier-aware** (35 dla T1/2, 40 dla T3) + uwzględnić paczki firmowe. Trójka plików (feasibility + symulator + plan-recheck) **razem**.

### Faza 5 — PRZED WŁĄCZANIEM FUNKCJI (gdy nowe miasto/Telegram) (dni)
- **R1** zamek na `pending_proposals`. **R2** naprawić postpone. **R4** zapis + wyjątek bramki dla kary po-zmianowej. *Reguła Załącznika C2: włączenie funkcji / re-enable serwisu = PEŁNY deploy, nie „tylko flaga".*

### Faza 6 — WIELKI KAMIEŃ FRANCZYZY: multi-city (sprinty) (tygodnie)
- **F1 — ✅ AUDYT ZROBIONY 28.06.** Pełne dokumenty: `ZIOMEK_MULTICITY_INVENTORY.md` (~70 sprzężeń, 8 twardych P0) + `ZIOMEK_MULTICITY_ARCHITECTURE.md` (decyzja + playbook nowego miasta) + `ZIOMEK_MULTICITY_FINDINGS.json`.
- **Wynik (decyzja do akceptacji): proces-per-miasto na WSPÓLNYM kodzie (Opcja C).** NIE budujemy `tenant_id` (to byłby ogromny refaktor walczący z każdym singletonem/lockiem). Budujemy jeden korzeń konfiguracji per miasto (`STATE_ROOT` / `CONFIG_PATH` / `FLAGS_PATH` / `OSRM_BASE` / `PANEL_BASE_URL`) + osobny komplet usług systemd (`name@<city>.service`). Dwa „seamy" już istnieją (`DISPATCH_FLAGS_PATH`, częściowo `DISPATCH_STATE_DIR`).
- **8 twardych P0 = 2 decyzje:** (a) infra routingu (osobny OSRM z mapą miasta + bbox „poison-filter"), (b) tenancy (globalny `dispatch_state/` 433 literały → helper `state_path()`, `config.json` env-selectowalny, panel host/creds per-miasto, szablony systemd, sekrety/bot per-miasto).
- **MOAT = wiedza geograficzna per-miasto (P1):** tabela dzielnic + graf sąsiedztwa + krzywa korków + roster/tiery — bez nich Ziomek „jeździ jak turysta" (degradacja miękka, nie crash). To uczy się/buduje per miasto.
- **Sprint techniczny F1** (kolejny krok po akceptacji modelu): Faza A = haki konfiguracji u źródła (przez Przykazanie #0, mapa kompletności: `state_path()` zamiast 433 literałów, `DISPATCH_CONFIG_PATH`, `OSRM_BASE`, `PANEL_BASE_URL`, `METRO_BBOX` env, `BIALYSTOK_CENTER`→`geo.city_center` w 5 plikach RAZEM) → Faza B = paczka geo + bootstrap restauracji/rostera per-miasto → Faza C = cold-start kalibracji (kopia priora Białystoku) + szablony systemd → Faza D = shadow 14–30 dni + walidacja gotowości → go-live miasta #2.

### W tle — DŁUG (kiedy są wolne ręce)
- **D1** zlać kolejność trasy do jednej funkcji (po B1). **D2** stare kubełki. **D3** `.bak`/martwy kod/flagi/timery. **D4** TTL pozycji od właściwego czasu.

---

## 5. Jedna kartka „dlaczego to jest moat" (do zapamiętania)
- Konkurencja ma „przypisz najbliższego". My mamy system, który **dispatchuje jak doświadczony koordynator-weteran**, bo jest skalibrowany pod realny korek, realne tempo kurierów i stygnące jedzenie — i robi to **autonomicznie**.
- Żeby ten moat działał w 10 miastach, musi być: **spójny na ekranach** (filar 1), **przejrzysty** (filar 2), **konfigurowalny per-miasto** (filar 3), **bezpieczny w zmianie** (filar 4), **samo-kalibrujący** (filar 5).
- Plan wyżej jest właśnie tym: najpierw bezpieczeństwo zmian i widoczność (żeby franczyza nie psuła się po cichu), potem żywe bugi, potem wielki kamień multi-city.

---

## 6. Następny ruch (rekomendacja)
1. Pozwól sesji 17 domknąć **B1** (deploy za ACK).
2. Daj zielone na **Fazę 1 (F3)** i **Fazę 2 (F2)** — tanie, fundamentalne, odblokowują dowody i dashboard.
3. **Audyt multi-city (F1) — ✅ zrobiony 28.06** (`ZIOMEK_MULTICITY_INVENTORY.md` + `…_ARCHITECTURE.md`). Następny ruch: **zaakceptuj model „proces-per-miasto"** (Opcja C) → wtedy ruszamy sprint techniczny F1 Faza A (haki konfiguracji u źródła, przez Przykazanie #0).

> Wszystkie naprawy: przez Przykazanie #0. Wątpliwość co do priorytetów/inwersji → pytamy, nie zgadujemy. Częściowa zmiana = niezakończona.
