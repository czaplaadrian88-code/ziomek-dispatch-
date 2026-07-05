# 07 — ROADMAPA integracji (priorytetyzacja · etapy · ryzyka · metryki)

> Wielki Audyt Integracji — FAZA 5A–5D. Data: 2026-07-05 (start = Q3 2026).
> Założenie zasobowe: 1 inżynier full-time (AI-wspomagany) + Adrian (BD/decyzje) + wsparcie punktowe; nakłady w osobotygodniach (ot) z artefaktów 03/05.

## 5A — PRIORYTETYZACJA

Ocena 1–5 (5 = najlepiej). Zasięg = liczba restauracji osiągalnych w PL; ROI uwzględnia realność wolumenu dla pokrycia Białystok/Podlasie.

| Rank | Integracja | Łatwość | Zasięg PL | ROI | Koszt wejścia | Przewaga konkur. | Wynik | Uwaga |
|---|---|---|---|---|---|---|---|---|
| 1 | **IR v1 (pakiet gotowości)** | — | — | — | 10–14 ot | — | **warunek wszystkiego** | bez tego reszta nie istnieje |
| 2 | **Restimo (P1/W2)** | 4 | 5 | 5 | 3–5 ot + [DW] | 4 (okno: dispatch PL słabo obsadzony) | ⭐ 18 | proces BD od dnia 1 |
| 3 | **Restaumatic Giełda (P2)** | 3 [kontrakt DW] | 5 (~5000) | 5 | BD + adapter | 4 (jawne okno „kolejni dostawcy") | ⭐ 17 | proces BD od dnia 1 |
| 4 | **Self-delivery agregatorów (pakiet handlowy)** | 5 (zero kodu ponad IR v1) | 4 (każda restauracja na Pyszne/Glovo) | 5 | ~0 | 5 („zejdź z 30% prowizji") | ⭐ 19 (ale zależne od sprzedaży, nie techniki) | quick-win handlowy #1 |
| 5 | **WooCommerce (W3)** | 5 | 2 | 3 | 3–4 ot | 3 (demo publiczne) | 13 | quick-win techniczny #1 |
| 6 | **DIY kit + developer portal (W10)** | 5 | 2 (długi ogon) | 4 (wiarygodność BD) | 1–2 ot | 4 | 15 | wchodzi w IR-5 |
| 7 | **UpMenu (P3)** | 3 | 4 | 4 | BD + adapter | 3 | 14 | BD równolegle |
| 8 | **GoPOS (P4)** | 2 (slot zajęty) | 5 | 4 | BD + [DW] | 3 | 14 | BD równolegle, dłuższy proces |
| 9 | **Poster (W4)** | 5 | 1 | 2 | 2–3 ot | 2 | 10 | tani dowód; tylko przy wolnym slocie |
| 10 | **Dotykačka (W5)** | 4 | 2 | 2 | 3–4 ot | 2 | 10 | jw. |
| 11 | **Deliverect Dispatch (W1/P5)** | 4 | 1 dziś / 4 przy ekspansji | 2 dziś / 5 potem | 3–4 ot + [DW] | 3 | 10→16 | budować przy ekspansji >1 miasto |
| 12 | **POSbistro / Zamów.online (P6/P7)** | 3 | 3 | 3 | BD | 2 (Restimo i tak ich łapie) | 11 | tylko jeśli Restimo odmówi/opóźni |
| 13 | Lightspeed (W9/P11) | 2 | 2 | 2 | 4–6 ot | 2 | 8 | druga linia |
| 14 | Shopify/Presta (W6/W7) | 3 | 1 (gastro) | 2 (paczki+) | 4–6/3–4 ot | 2 | 8 | przy torze paczkowym/e-com |
| 15 | HubRise (P10) | 4 | 1 | 1 dziś | 2–3 ot | 2 | 8 | ekspansja EU |
| 16 | Teya/Storyous (P9) | 2 | 2 | 2 | BD | 2 | 8 | wolny proces korpo |
| 17 | Oracle Simphony (P12) | 1 | 1 (bez klienta) | 1→5 z klientem | OPN [DW] | 4 przy sieci | warunkowy | tylko pod klienta kotwicznego |

**QUICK WINS (maks. zasięg / min. nakład):** (1) pakiet handlowy self-delivery agregatorów — zero kodu ponad IR v1, sprzedaż od razu po sandboxie; (2) maile/telefony P1–P4 — koszt 0, uruchamiają najdłuższe zegary; (3) WooCommerce + DIY kit — publiczne demo za ~5 ot, waliduje IR v1 end-to-end i służy jako materiał do rozmów partnerskich.

## 5B — ROADMAPA FAZOWA (kwartalna)

Zależność nadrzędna: **IR v1 najpierw; procesy partnerskie startują równolegle od dnia 1** (trwają najdłużej). Odniesienia: IR-0…IR-6 z `03-ANALIZA-LUK.md`, W*/P* z `05`/`06`.

### ETAP 1 — Quick wins (lip–wrz 2026, 1–3 mies.)
- **Tydzień 1–2:** IR-0 (sieć: host-firewall, bindy — S) + IR-1 (higiena /v1, błędy, idempotencja — M) + **wysłane zapytania partnerskie P1–P4** + research brief (pytania 🔴) przez Perplexity/kontakty.
- **Tydzień 3–8:** IR-2 (API DaaS v1 — L) równolegle z IR-3 (kanon zdarzeń — L; sekwencja włączenia: IR-3 przed publicznym IR-4).
- **Tydzień 8–11:** IR-4 (worker webhooków — M) + IR-5 (sandbox+docs+onboarding — M/L) + IR-6 (DPA — S, prawnik zewn.).
- **Tydzień 11–13:** W10 DIY kit + W3 WooCommerce (demo publiczne); pilotaż z 1–2 restauracjami referencyjnymi na self-delivery agregatora.
- **Zasoby:** 1× backend full-time (IR); frontend punktowo (panel Integracje, portal docs ~2 ot); DevOps punktowo (firewall, nginx, systemd ~0,5 ot); Adrian: BD P1–P4 + decyzje ACK (flipy flag wg protokołu Ziomka).
- **Koszty zewn.:** prawnik (DPA/umowa) ~2–5 tys. zł; domena/hosting docs ~0; ubezpieczenie OC przewoźnika — do wyceny.
- **Wyjście etapu:** działające publiczne API v1 + sandbox + docs + 1 wtyczka publiczna + odpowiedzi partnerów P1–P4.

### ETAP 2 — Kluczowe integracje PL (paź–gru 2026, 3–6 mies.)
- **Restimo (W2)** i/lub **Restaumatic** — adaptery wg otrzymanych kontraktów (3–5 ot każdy); certyfikacje.
- **UpMenu (P3)** — adapter jeśli proces dojrzał; **GoPOS (P4)** — kontynuacja procesu (realnie zamknięcie w Etapie 3).
- Pakiet self-delivery agregatorów — skala sprzedażowa (cel: 20–30 restauracji Białystok).
- SHOULD z 03: GPS-push `courier.location_updated` (L17 — wyróżnik vs Glovo), POD etap 1 (L18: `pod{delivered_at,gps}` — dane już są), monitoring per connection (L19).
- **Zasoby:** 1× backend (adaptery+SHOULD), Adrian BD; support: pierwsza linia dla integratorów (~0,25 etatu od 1. partnera live).
- **Koszty:** ewentualne opłaty partnerskie Restimo/Restaumatic [DW]; wyjazdy/demo.
- **Wyjście:** ≥1 hub/giełda LIVE z realnymi zleceniami + ≥30% zleceń wpada automatycznie.

### ETAP 3 — Huby globalne i długi ogon (sty–cze 2027, 6–12 mies.)
- **Deliverect Dispatch (W1/P5)** — budowa+certyfikacja (warunek sensu: decyzja o ekspansji poza Białystok lub klient sieciowy).
- GoPOS domknięcie; Poster/Dotykačka (W4/W5) jeśli popyt; POSbistro/Zamów.online, jeśli Restimo nie pokrył.
- Tor paczkowy: Shopify/Presta (W6/W7) dla e-com lokalnego.
- **Program L21-etap-2 (obejście gastro)**: start projektu XL — bezpośredni ingest do silnika; kontrakt /v1 bez zmian (architektura 04 §4.3).
- **Zasoby:** 1–2× backend; support 0,5 etatu.
- **Wyjście:** 5+ aktywnych konektorów; SLA webhooków ≥99%; gastro schodzi ze ścieżki krytycznej nowych kanałów.

### ETAP 4 — Poziom „Wolt Drive" (H2 2027+)
- Pełny self-serve onboarding (klucz+sandbox+go-live bez człowieka), katalog partnerów na stronie, program deweloperski (4C-2 jako strategia, nie tylko higiena), OAuth2 (L23), POD-foto (L18 etap 2, sprint apki), zwroty `related_deliveries` (L22), multi-miasto w `delivery-areas`, ewentualny broker za outboxem (>10 zleceń/s).
- **Wyjście:** restauracja lub POS podłącza się bez naszego udziału; setki integracji utrzymywane kontraktem, nie kodem per partner.

**Koszt utrzymania per integracja (planować od Etapu 2):** ~0,5–1 ot/kwartał na wtyczkę własną (wersje platform), ~0,25 ot/kwartał na adapter partnerski (zmiany kontraktu), + support 1. linii rosnący z liczbą połączeń — przy 10 integracjach ≈ 0,5 etatu inżynierskiego łącznie.

## 5C — RYZYKA I MITYGACJE (każde z planem B)

| Ryzyko | Prawdopod./wpływ | Mitygacja | Plan B |
|---|---|---|---|
| **Zmiana API partnera bez wypowiedzenia** | Śr./Wys. | wersjonowanie po NASZEJ stronie (/v1 stabilne), testy kontraktowe adapterów w CI, monitoring per connection (L19) alarmuje na 4xx/5xx | adapter naprawiamy niezależnie od rdzenia (architektura konektorowa); polling fallback GET /deliveries |
| **Dostawca POS/hub blokuje nas z powodów konkurencyjnych** (GoPOS ma Wolt Drive; huby mają własne interesy) | Śr./Wys. | dywersyfikacja kanałów (Restimo + Restaumatic + direct + własne wtyczki + self-delivery); umowy bez wyłączności; relacja przez wspólnych klientów-restauracje | przenosimy restauracje danego POS na kanał DIY-widget/Make; eskalacja przez restauracje („klient żąda opcji") |
| **Klauzule wyłączności w umowach hubów** | Nis-Śr./Wys. | czerwona linia negocjacyjna: NIE podpisujemy wyłączności; prawnik czyta każdą umowę | rezygnacja z kanału — mamy alternatywy |
| **Rate limiting / throttling po ich stronie** | Śr./Nis. | backoff w adapterach (już wzorzec), kolejka outbox wyrównuje piki | degradacja do rzadszych aktualizacji ETA; statusy krytyczne priorytetem |
| **Zależność od uptime'u zewnętrznych systemów** (w tym NASZEGO gastro w ścieżce — L21!) | Śr./Wys. | circuit breaker per connection; `needs_review` zamiast utraty; tryb awaryjny OPS-10; SLA komunikowane uczciwie | Etap 3: program obejścia gastro; ręczna konsola koordynatora zawsze działa |
| **Desynchronizacja danych (statusy kłamią u partnera)** | Śr./Wys. | JEDEN kanon zdarzeń (IR-3) przed włączeniem webhooków; `status_corrected` przy resurrect; event_id dedup; testy parytetu kanon↔webhook | endpoint rekoncyliacji GET /deliveries/{id} + nightly diff report |
| **RODO: incydent z danymi klienta końcowego** | Nis./Wys. | DPA przed startem (IR-6), minimalizacja (tracking już min-PII), szyfrowanie credentiali, retencja+erase (jest) | procedura zgłoszenia 72h; rejestr przetwarzania aktualny |
| **Partner ignoruje/odrzuca (Restimo, Restaumatic nie odpisują)** | Śr./Śr. | wejście przez wspólne restauracje (pull od klienta), demo działające, pilot regionalny jako niski próg | plan B = kolejny kanał z rankingu; self-delivery agregatorów nie wymaga niczyjej zgody |
| **Konkurent (Wolt Drive/DeliGoo) zajmuje kafelki zanim wejdziemy** | Wys./Śr. | szybkość: BD od dnia 1; argument lokalny (pokrycie, cena, elastyczność, COD) | nisze: godziny/strefy poza ich zasięgiem, paczki+gastro łączone, sieci lokalne |
| **Przeciążenie zespołu (1 inżynier)** | Wys./Śr. | sekwencja IR ściśle wg zależności; wtyczki W4–W7 tylko przy popycie; AI-wspomaganie; zamrożenie zakresu v1 | przesunięcie Etapu 2 zamiast cięcia jakości IR (webhooki kłamiące = spalony rynek) |

## 5D — METRYKI SUKCESU (dashboard od Etapu 1)

| Metryka | Definicja | Cel E1 (wrz'26) | Cel E2 (gru'26) | Cel E3 (cze'27) |
|---|---|---|---|---|
| Aktywne konektory | connections z ≥1 zleceniem/30 dni | 1 (Woo/DIY pilot) | 3 | 5+ |
| **% zleceń automatycznych** | zlecenia bez ręcznego przepisywania / wszystkie (mierzalne: `source_channel` ≠ manual/phone) | 10% | 30% | 60% |
| Czas podłączenia restauracji | od klucza API do 1. zlecenia live | <5 dni | <2 dni | <1 dzień (self-serve) |
| Wolumen per kanał | zlecenia/mies. per connection | baseline | rosnący m/m | — |
| Uptime integracji | webhooki delivered/(delivered+failed) + API 5xx rate | 99% | 99,5% | 99,9% |
| Czas dostarczenia webhooka | p95 event→ACK partnera | <60 s | <30 s | <15 s |
| Onboarding partnerski | procesy P* w toku / zamknięte | 4 wysłane | 2 podpisane | 4 podpisane |

Źródła danych: `OutboundDeliveryLog`, `InboundOrderEvent`, `source_channel` w Delivery, monitoring L19. Przegląd metryk co 2 tygodnie przy roadmapie.
