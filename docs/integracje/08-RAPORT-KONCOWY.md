# 08 — RAPORT KOŃCOWY: od naszego API do modelu Wolt Drive

> Wielki Audyt Integracji — FAZA 5E. Data: 2026-07-05. Pełen materiał dowodowy: artefakty `00`–`07` + `99-RESEARCH-BRIEF.md` w tym katalogu.

---

## EXECUTIVE SUMMARY (1 strona)

**Cel:** restauracja klika „Przyjmij i wyślij kuriera" w SWOIM systemie → zlecenie trafia do nas automatycznie, statusy i tracking wracają. Poziom odniesienia: Wolt Drive / Glovo On-Demand / Uber Direct.

**Gdzie jesteśmy (audyt kodu, FAZA 0):** mamy więcej, niż sądziliśmy — w panelu istnieje uśpiona warstwa integracyjna (klucze API per połączenie, przyjmowanie zleceń z idempotencją, model webhooków z HMAC, wycena, legacy kontrakt PrestaShop) — wyłączona flagami. Mamy też tracking klienta na poziomie liderów (`/t/{token}` + SMS). Braki krytyczne: nie ma workera wysyłającego webhooki (statusy nie wychodzą pushem), publiczne API nie obejmuje toru jedzenia, wycena nie jest spięta ze zleceniem, a status zlecenia żyje w trzech miejscach naraz (legacy gastro — scrapowany HTML — jest operacyjnym źródłem prawdy). Checklist gotowości: 1× TAK, 7× CZĘŚCIOWO, 5× NIE.

**Rynek (FAZY 1–2):** slot „wyślij kuriera z systemu restauracji" w Polsce już istnieje i jest obsadzany przez Wolt Drive, Uber Direct, Glovo, Stavę i DeliGoo (DeliGoo działa w Białymstoku). Nie tworzymy kategorii — dołączamy do istniejących kafelków wyboru kuriera. Największa dźwignia to warstwa pośredników: **Restimo** (jedna integracja → GoPOS, POSbistro, Dotykačka, SOGA, S4H, LSI + 5 agregatorów) i **Giełda Kurierów Restaumatic** (~5000 restauracji, jawnie „kolejni dostawcy wkrótce"). Standard techniczny branży jest konwergentny i dokładnie opisany w `02-BENCHMARK.md` — wiemy co budować co do pola.

**Decyzja rekomendowana:** zbudować w Q3'26 pakiet **Integration Readiness v1** (~10–14 osobotygodni: publiczne API quote→create z idempotencją, jeden kanon statusów, worker webhooków z podpisem, sandbox z symulatorem, docs, self-service kluczy, DPA) i RÓWNOLEGLE od pierwszego tygodnia uruchomić 4 procesy partnerskie (Restimo, Restaumatic, UpMenu, GoPOS). Najtańszy wolumen natychmiastowy: pakiet handlowy „self-delivery na agregatorach — my jesteśmy Twoją flotą" (zero dodatkowego kodu). Koszt zewnętrzny Q3: głównie prawnik (DPA) i czas BD.

**Czego NIE robić:** nie budować 10 integracji direct-do-POS przed hubami; nie podpisywać wyłączności; nie wchodzić w GloriaFood (wygaszane), Domino's (zamknięte), DoorDash (brak PL); nie włączać webhooków przed domknięciem kanonu statusów (kłamiące statusy = spalony rynek).

---

## ODPOWIEDZI NA PYTANIA ZARZĄDU

### 1. Jak zbudować sieć integracji porównywalną z Wolt Drive?
Trzema warstwami, w tej kolejności: **(a) fundament** — jedno publiczne API w standardzie branżowym (IR v1; szczegóły `03`), którego kontrakt jest stabilny niezależnie od tego, co wymieniamy w środku; **(b) dystrybucja cudzymi rękami** — huby i giełdy (Restimo, Restaumatic, potem Deliverect), gdzie restauracja włącza nas samoobsługowo; **(c) własne kanały** — wtyczki (WooCommerce, Poster, Dotykačka) + DIY kit dla restauracji z devem. Wolt ma 50+ partnerów integracyjnych, bo najpierw zrobił (a), a potem katalog „wybierz swój POS" — kopiujemy tę sekwencję w skali PL.

### 2. Które integracje wdrażamy jako pierwsze i dlaczego?
(1) **Self-delivery agregatorów** — pakiet handlowy bez kodu: restauracja na Pyszne/Glovo/Bolt przechodzi na własną dostawę (niższa prowizja), a flotą jesteśmy my; (2) **Restimo** — jedna integracja otwiera większość POS-ów PL naraz, a kategoria kurierska nie jest tam jeszcze zabetonowana; (3) **Restaumatic** — największa baza restauracji z własnym kanałem i jawnie otwarte okno na Giełdzie; (4) **WooCommerce + DIY kit** — tani, w pełni nasz kanał i publiczne demo dla partnerów. Uzasadnienie liczbowe: ranking w `07-ROADMAPA.md` §5A.

### 3. Które budujemy w pełni samodzielnie?
IR v1 (całość), wtyczka WooCommerce, DIY kit/portal deweloperski, aplikacja Poster, konektor Dotykačka, moduł Make — wszystko bez niczyjej zgody (ścieżka `SAMI`). Konektor Deliverect Dispatch budujemy sami wg ich publicznej specyfikacji, ale aktywacja wymaga rejestracji partnerskiej.

### 4. Gdzie konieczne są partnerstwa i z kim?
Restimo (kategoria „courier service"), Restaumatic (Giełda Kurierów), UpMenu, GoPOS (natywny slot), POSbistro/Zamów.online (jeśli Restimo nie pokryje), Deliverect (Dispatch, przy ekspansji), Lightspeed/Teya (druga linia), Oracle (tylko pod klienta sieciowego). Tabela z kanałami kontaktu, pitchami i pytaniami: `06-PARTNERSTWA-I-KONTAKTY.md`. Zasada: żadnych wyłączności.

### 5. Największy wzrost liczby restauracji / najtaniej / największa przewaga?
- **Największy wzrost:** Restimo i Restaumatic (tysiące restauracji za jedną integracją każde).
- **Najtaniej:** self-delivery agregatorów (0 kodu) i DIY kit/Woo (~5 ot łącznie).
- **Największa przewaga konkurencyjna:** pokrycie lokalne + COD (Uber Direct nie ma COD; my i Wolt mamy), GPS-push pozycji kuriera (Glovo nie ma), elastyczność małego operatora (czasówki, ustalenia indywidualne, onboarding ≤3 dni jak Deligoo) oraz łączony tor jedzenie+paczki, którego nie ma żaden z konkurentów kurierskich w PL.

### 6. Architektura za 3–5 lat (tysiące restauracji, setki integracji)?
Kontrakt publiczny `/v1` + katalog zdarzeń są niezmienne; wszystko za portem wejściowym (`ingest_inbound_order`) i strumieniem zdarzeń (`StatusEvent`) jest wymienne. Konektory = osobne procesy z konfiguracją w bazie (nie w kodzie), dodawane bez dotykania rdzenia. Skalowanie etapami: outbox w Postgres → broker (>10 zleceń/s) → multi-miasto w `delivery-areas` → SDK konektora dla zewnętrznych deweloperów. Legacy gastro schodzi ze ścieżki krytycznej w Etapie 3 bez zmiany kontraktu dla partnerów. Szczegóły i diagram: `04-ARCHITEKTURA-DOCELOWA.md`.

### 7. Strategia „chicken & egg" — jak równolegle przekonywać restauracje i dostawców systemów?
Po stronie restauracji: sprzedajemy WYNIK, nie integrację („zejdź z 30% prowizji na self-delivery", „zero przepisywania zamówień") — pilotaże referencyjne w Białymstoku dają liczby (dostawy/mies., czas, oceny). Po stronie dostawców systemów: przychodzimy z gotowym, klikającym się produktem (sandbox+docs+demo Woo) i z **popytem od wspólnych klientów** — restauracja, która u nich pracuje i chce naszego kuriera, jest najskuteczniejszym argumentem („klient żąda opcji"). Każdy pilot restauracyjny wybieramy więc tak, by pracował na systemie, z którym chcemy rozmawiać (GoPOS/Restaumatic/UpMenu). Koło zamachowe: pilot → case study → partner → kolejne restauracje partnera.

### 8. NASTĘPNE KROKI

**14 dni (do 2026-07-19):**
1. ☐ Decyzja zarządu: zatwierdzenie pakietu IR v1 + budżetu (prawnik DPA, ubezpieczenie OC) — właściciel: Adrian.
2. ☐ Wysłanie 4 zapytań partnerskich: Restimo (hello@restimo.com), Restaumatic (BD +48 732 081 111), UpMenu (formularz), GoPOS (office@gopos.pl) — pitch i pytania gotowe w `06` §4B — właściciel: Adrian.
3. ☐ Research brief: pytania 🔴 (#1–#5, #34) przez Perplexity/kontakty; wynik aktualizuje artefakty — właściciel: Adrian + sesja CC.
4. ☐ Start IR-0 (host-firewall, bindy lokalne — wg protokołu zmian Ziomka, z ACK) + IR-1 (prefiks /v1, model błędów, idempotencja) — właściciel: inżynieria.
5. ☐ Konto sandbox Stuart (~15 min, self-service) — pobrać enumy/OpenAPI do domknięcia benchmarku — właściciel: inżynieria.
6. ☐ Wytypowanie 2–3 restauracji pilotażowych pod self-delivery agregatora (kryterium: system = GoPOS/Restaumatic/UpMenu) — właściciel: Adrian.

**30 dni (do 2026-08-04):**
7. ☐ IR-2 w budowie (API quotes+deliveries na istniejącej ścieżce OPS-02); przegląd kontraktu vs `02-BENCHMARK.md` „Standard branżowy".
8. ☐ IR-3 zaprojektowany i rozpoczęty (most silnik→StatusEvent; maszyna przejść jedzenia) — TO JEST ŚCIEŻKA KRYTYCZNA.
9. ☐ Zlecenie prawnikowi: wzorzec umowy partnerskiej + DPA (IR-6).
10. ☐ Pierwsze rozmowy z partnerami odbyte; warunki Restimo/Restaumatic znane → aktualizacja rankingu i roadmapy (07).
11. ☐ Pilot self-delivery: 1. restauracja wożona przez nas na własnej dostawie agregatora; pomiar KPI od 1. dnia.
12. ☐ Przegląd postępu vs metryki 5D; korekta planu Q4.

---

## Kryterium sukcesu audytu (Definicja ukończenia — spełniona)
Trzy decyzje możliwe bez dodatkowej analizy: **(1) co budujemy w tym kwartale** — IR v1 + W10/W3 (artefakty 03/05/07); **(2) do kogo piszemy w tym tygodniu** — Restimo, Restaumatic, UpMenu, GoPOS (artefakt 06, gotowe pitche i pytania); **(3) czego nam brakuje technicznie** — 24 luki z priorytetami i pakiet IR v1 (artefakt 03). Pozycje niezweryfikowane mają pytania w `99-RESEARCH-BRIEF.md` (44 pytania, w tym 8 blokujących 🔴).
