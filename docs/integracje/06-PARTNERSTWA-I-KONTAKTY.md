# 06 — STRATEGIA PARTNERSTW I KONTAKTY (+ ścieżki alternatywne)

> Wielki Audyt Integracji — FAZA 4B/4C. Data: 2026-07-05.
> Zakres: systemy `PARTNER` + huby z `01-MAPA-RUNKU.md`. Statusy źródeł: [ZW]/[DW] jak w całym audycie.
> **Wspólny warunek wejścia we WSZYSTKIE rozmowy:** gotowy pakiet IR v1 (sandbox + docs + działający webhook) i wtyczka-demo (Woo/DIY kit) — partner musi móc kliknąć, nie tylko słuchać.

## 4B — TABELA PARTNERSTW (kolejność = priorytet)

| # | Firma | Cel biznesowy kontaktu | Kanał kontaktu [status] | Co mieć gotowe | Pitch jednym zdaniem | Kluczowe pytania | Szac. czas procesu | Koszty [status] |
|---|---|---|---|---|---|---|---|---|
| P1 | **Restimo** | wejście do kategorii „courier service" (obok Wolt Drive/Stava/DeliGoo) = 1 integracja → większość POS PL + 5 agregatorów | hello@restimo.com, +48 732 054 390; restimo.com/become-a-partner; docs.restimo.com [ZW] | IR v1 + sandbox + cennik stref Białystok + SLA (czas przyjęcia, % on-time) | „Lokalny kurier on-demand z pełnym API (quote/statusy/tracking GPS) — dowozimy tam i wtedy, gdzie duzi nie chcą, z lepszą stawką." | Format kontraktu API dostawcy? Koszt/rewshare? Certyfikacja i czas? Czy Wolt Drive/Uber Direct są już wpięci jako dispatch? Kiedy „Own fleet management"? Ekskluzywność? | 4–8 tyg. [HIP] | [DW — pyt. #1] |
| P2 | **Restaumatic** | miejsce na „Giełdzie Kurierów" (~5000 restauracji; jawne okno „kolejni dostawcy wkrótce") | BD +48 732 081 111; formularz restaumatic.com/pl/courier-marketplace [ZW] | jw. + porównanie stawek vs Wolt Drive/Stava/DeliGoo | „Kolejny dostawca na Giełdę: konkurencyjna stawka, pokrycie Podlasie, API w standardzie który już znacie (jak Deligoo)." | API dostawcy (zlecenia in, cena/ETA out, statusy)? Warunki prowizyjne Giełdy? Wymogi SLA/ubezpieczenie? Pilotaż regionalny (Białystok) możliwy? | 4–8 tyg. [HIP] | [DW — pyt. #4] |
| P3 | **UpMenu** | dostawca delivery obok Stava/Glovo OD/Uber Direct/Shipday | formularz upmenu.com (integracje) / support techniczny [ZW że per-partner] | jw. | „Ta sama półka co Stava — ale on-demand z GPS-trackingiem i API bez pośredników." | Aktualne docs REST API + webhook order.created (adres+telefon)? Sandbox? Proces zostania delivery providerem? | 3–6 tyg. [HIP] | [DW — pyt. #6] |
| P4 | **GoPOS** | natywny slot kuriera w POS (obok Wolt Drive/Stava/DeliGoo) + obecność w GoHub | gopos.pl/integrators (formularz Integrator), office@gopos.pl, +48 790 295 124 [ZW] | IR v1 + case study pilotażu (najlepiej wspólna restauracja referencyjna) | „Wasi klienci w Podlaskiem nie mają dziś realnej opcji kuriera — my domykamy mapę pokrycia." | Czy slot kuriera otwarty dla nowych? Webhooki push + sandbox? Koszt/czas certyfikacji? Model rozliczeń integracji? | 6–10 tyg. [HIP] | [DW — pyt. #7] |
| P5 | **Deliverect** | partner Dispatch (globalna dystrybucja; przygotowanie pod ekspansję poza Białystok) | deliverect.com/en/become-a-partner; developers.deliverect.com [ZW] | konektor W1 zbudowany na sandbox + pokrycie >1 miasta (inaczej słaba karta) | „Polski last-mile dla waszych klientów poza zasięgiem Wolt Drive — pełna implementacja waszego kontraktu Dispatch." | Kryteria certyfikacji + czas? Model komercyjny partnera? Wymogi pokrycia geograficznego? Auth webhooków (HMAC?)? | 6–12 tyg. [HIP] | [DW — pyt. #2] |
| P6 | **POSbistro** | integracja bezpośrednia lub potwierdzenie ścieżki przez Restimo | partners@posbistro.com, +48 12 345 12 87 [ZW] | jw. | „Wasze restauracje z dowozem dostają kuriera z poziomu tabletu — bez zmiany nawyków." | Publiczne API/webhooki/sandbox? Direct czy tylko Restimo? | 4–8 tyg. [HIP] | [DW — pyt. #9] |
| P7 | **Zamów.online** | dostawca „do kuriera" (direct lub przez Restimo) | formularz zamow.online [ZW] | jw. | „Zamówienie z waszego systemu → nasz kurier → status wraca; zero przepisywania." | Własne API czy wszystko przez Restimo? | 3–6 tyg. [HIP] | [DW — pyt. #11] |
| P8 | **Dotykačka** (listing oficjalny) | oficjalna integracja w ich katalogu (dev można zacząć bez zgody) | wsparcie.dotykacka.pl / manual.dotykacka.cz (kontakt integracyjny) [ZW] | konektor W5 działający na kluczu testowym | „Gotowa integracja kurierska dla waszych klientów gastro w PL — do listingu." | Webhooki? Zapis statusu dostawy do zlecenia? Warunki listingu? | 4–8 tyg. [HIP] | prawdopodobnie 0 [HIP] |
| P9 | **Teya/Storyous** | slot dostawcy w ekosystemie Teya (PL/CEE) | developer.teya.com / Teya Partners [ZW] | IR v1 | „On-demand delivery dla restauracji Storyous w PL — przez wasze API." | Czy API obejmuje zamówienia (nie tylko płatności)? Proces partnerski? | 8–16 tyg. (korporacja) [HIP] | [DW — pyt. #13] |
| P10 | **HubRise** | listing aplikacji delivery (EU; przygotowanie ekspansji) | hubrise.com/apps — rejestracja aplikacji [ZW; samoobsługa DW] | konektor (podobny do W1) | „Kolejna delivery-app w katalogu — pokrycie PL." | Samoobsługowa rejestracja? Klienci PL? | 2–6 tyg. [HIP] | [DW — pyt. #5] |
| P11 | **Lightspeed** | zatwierdzenie API client + listing Partner integrations | kseries.api@lightspeedhq.com; api-portal.lsk.lightspeed.app [ZW] | konektor W9 (po potwierdzeniu payloadu) | „Kurier on-demand dla klientów K-Series w PL." | Payload zamówienia (adres+telefon)? Scope? Listing? | 4–10 tyg. [HIP] | niski [HIP] |
| P12 | **Oracle Simphony** | TYLKO pod konkretnego klienta sieciowego/hotelowego (ciężka certyfikacja OPN) | Oracle PartnerNetwork → ISV [ZW] | klient kotwiczny + IR v1 | „Wasz wspólny klient X potrzebuje naszego last-mile — certyfikujemy się pod niego." | Koszt OPN? Czas walidacji? Kategoria dispatch w marketplace? | 3–6 mies. [HIP] | OPN płatne [DW — pyt. #17] |

**Zasada sekwencji:** procesy P1–P4 startują RÓWNOLEGLE od dnia 1 (trwają najdłużej, a nie blokują budowy IR v1). P5/P10 po zbudowaniu W1. P12 tylko reaktywnie.
**Czego NIE robimy:** partnerstwa z Wolt/Uber/Glovo/Stuart/DeliGoo/Stava (konkurenci — patrz 4C-1 ryzyko); Papu.io tylko monitorujemy (konkurent software'owy; ewentualnie integr-in ich zamówień, jeśli restauracja tego zażąda).

## Pakiet „przed pierwszą rozmową" (wspólny dla P1–P12)

1. **Techniczny:** działający sandbox + developers.nadajesz.pl (docs) + demo wtyczki Woo/DIY-widget + przykładowy webhook z podpisem.
2. **Handlowy:** cennik stref (Białystok + plan ekspansji), SLA (czas przyjęcia zlecenia, median czasu dostawy, % on-time — mamy dane z KPI panelu), godziny operacyjne, polityka anulacji, COD.
3. **Formalny:** wzorzec umowy + DPA/RODO (IR-6), ubezpieczenie OC, NIP-y/KRS.
4. **Referencyjny:** 2–3 restauracje referencyjne z pilotażu + liczby (dostawy/mies., czas, oceny z DeliveryRating).

---

## 4C — ŚCIEŻKI ALTERNATYWNE

### 4C-1. Hub jako klucz do wielu POS naraz (Restimo / Deliverect / HubRise)
**Co daje:** jedna integracja → dziesiątki POS + agregatory; zero BD z każdym POS-em osobno; restauracja włącza nas samoobsługowo. Restimo pokrywa PL (GoPOS, POSbistro, Dotykačka, SOGA, S4H, LSI, nomee + Pyszne/Glovo/Wolt/Uber Eats/Bolt Food); Deliverect pokrywa świat (Lightspeed, Toast, Square, Simphony…).
**Ograniczenia/ryzyka:** (a) hub kontroluje dostęp do popytu i kolejność kafelków — jesteśmy jedną z opcji obok Wolt Drive/DeliGoo; (b) rewshare/opłaty nieznane [DW]; (c) uzależnienie: wypowiedzenie umowy przez hub odcina cały kanał (mitygacja: równoległe ścieżki direct P2–P4 + własne wtyczki); (d) hub widzi nasze wolumeny i stawki (informacja konkurencyjna).
**Koszt:** integracja 3–5 ot (W1/W2) + koszty partnerskie [DO WERYFIKACJI].
**Kiedy ma sens zamiast direct:** ZAWSZE jako pierwszy krok przy naszej skali (jedna osoba techniczna nie obsłuży 10 procesów certyfikacji POS). Direct-integracje budujemy tylko tam, gdzie hub nie sięga (Restaumatic — własna Giełda) albo gdzie wolumen uzasadni pominięcie prowizji huba (GoPOS przy >X zleceń/mies.).
**Werdykt: TAK — Restimo jako kanał #1 PL, Deliverect jako #2 przy ekspansji.**

### 4C-2. Odwrócenie modelu: nasze publiczne API + katalog wtyczek + program dla deweloperów
**Co daje:** to ONI integrują się z NAMI (jak z Wolt Drive — 50+ partnerów przyszło do Wolta). Warunki minimalne już zdefiniowane: IR v1 + developers.nadajesz.pl + sandbox + DIY kit (W10) + katalog „nasze integracje" na stronie.
**Ograniczenia:** działa dopiero przy rozpoznawalności marki/pokryciu — nikt nie integruje się z kurierem jednego miasta z własnej woli; wymaga utrzymania docs/supportu deweloperskiego.
**Koszt:** praktycznie pokryty przez IR v1 (+1–2 ot na portal/katalog).
**Kiedy ma sens:** od dnia 1 jako HIGIENA (wiarygodność w rozmowach P1–P12 i samoobsługa dla DIY), ale jako STRATEGIA POZYSKANIA — dopiero po pokryciu >3 miast. Nie zastępuje partnerstw; jest ich warunkiem.
**Werdykt: TAK jako fundament, NIE jako główna strategia pozyskania w 2026.**

### 4C-3. Opcje awaryjne dla systemów `BRAK` (i restauracji bez żadnego systemu)
1. **Self-delivery na agregatorach (Pyszne/Uber Eats/Bolt Food):** restauracja przełącza się na własną dostawę, a „własną flotą" jesteśmy my. Zamówienie trafia do nas: (a) przez POS/middleware, do którego agregator już wpycha zamówienia (Restimo/POS z GoHub), (b) awaryjnie ręcznie z tabletu. Ryzyko: średnie (regulaminy agregatorów zwykle dopuszczają self-delivery — Glovo [DW pyt. #22]; brak formalnej relacji z agregatorem = mogą zmienić zasady). **Werdykt: TAK — to realnie największy wolumen „ukryty" w Białymstoku; sprzedawać jako pakiet „zejdź z 30% prowizji na self-delivery + my wozimy".**
2. **iPaaS (Make/Zapier — W8):** dla systemów z jakimkolwiek webhookiem/mailem. Ryzyko: niskie techniczne; kruchość scenariuszy po stronie klienta. **Werdykt: TAK, jako tani fallback (1–2 ot).**
3. **Eksport/import plików (CSV/mail-parser):** wzorzec już mamy (most Epaka/CSV, drtusz). Ryzyko: opóźnienia, brak statusów zwrotnych, koszt supportu. **Werdykt: TYLKO dla klientów o dużym wolumenie, na wyraźne żądanie, jako pomost do wdrożenia API — nie oferować aktywnie.**
4. **Półautomatyzacja „forward maila z zamówieniem":** parser maili potwierdzeń (GloriaFood-style systemy wysyłają maile). Ryzyko: wysokie (kruche parsowanie, RODO w skrzynce). **Werdykt: NIE na start; rozważyć tylko, jeśli konkretny system o istotnej bazie lokalnej nie ma innej drogi.**
5. **Zamknięte sieci (Domino's) i systemy martwe (GloriaFood):** **NIE wchodzić** — koszt/ryzyko bez zwrotu.

### Synteza 4C
Kolejność dźwigni: **(1) Restimo, (2) Restaumatic/UpMenu (giełdy), (3) własne wtyczki (Woo/DIY/Poster/Dotykačka), (4) self-delivery agregatorów jako pakiet handlowy, (5) Deliverect przy ekspansji, (6) Make jako fallback.** Eksporty plikowe i parsery — wyłącznie defensywnie. Wszystko stoi na jednym IR v1; żadna ścieżka nie wymaga osobnego silnika.
