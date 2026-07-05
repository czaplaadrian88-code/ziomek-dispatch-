# 99 — RESEARCH BRIEF: pytania do doweryfikowania (Perplexity / NotebookLM / kontakt bezpośredni)

> Zbiór wszystkich pozycji `[DO WERYFIKACJI]` z audytu. Każde pytanie gotowe do wklejenia. Po otrzymaniu odpowiedzi aktualizujemy `01-MAPA-RYNKU.md` (i kolejne artefakty).
> Stan: po FAZIE 1 (2026-07-05). Priorytet: 🔴 blokuje decyzje roadmapy · 🟡 ważne · ⚪ nice-to-know.

## 🔴 HUBY — decydujące o strategii

1. **[Restimo — dostawca kuriera]** Jakie są warunki wejścia do Restimo (restimo.com) jako dostawca usług kurierskich (kategoria „courier service" obok Wolt Drive, Uber Direct, Glovo On-Demand, Stuart, Stava, DeliGoo)? Czy istnieje formalny program dla delivery-providerów, jaki jest format API (REST? webhooki statusów?), koszt, model rozliczeń, SLA, czas certyfikacji? Co oznacza zapowiedź „Own fleet management — SOON" na restimo.com/en/integrations? *(alternatywa: kontakt hello@restimo.com, +48 732 054 390)*
2. **[Deliverect Dispatch — onboarding]** Jak dokładnie wygląda proces zostania partnerem „Dispatch" w Deliverect (developers.deliverect.com/docs/building-dispatch-integration): kryteria certyfikacji, czas onboardingu, model komercyjny (opłaty partnera), czy program dostępny dla przewoźnika działającego tylko w Polsce, wymagane SLA/pokrycie?
3. **[Wolt Drive / Uber Direct w Restimo]** Czy Wolt Drive i Uber Direct są już wpięci w Restimo jako zewnętrzny dispatch? (Jeśli nie — mamy okno pierwszeństwa.)
4. **[Restaumatic Giełda Kurierów]** Jaki jest techniczny i handlowy proces dołączenia jako dostawca kuriera do „Giełdy Kurierów" Restaumatic (restaumatic.com/pl/courier-marketplace) obok Wolt Drive/Uber Direct/Glovo/Stava/DeliGoo? Czy istnieje API dla dostawcy (przyjmowanie zleceń, zwrot ceny/ETA, statusy), czy integrację wykonuje ich zespół? *(kontakt BD: +48 732 081 111)*
5. **[HubRise]** Pełna lista delivery-providerów w katalogu HubRise (hubrise.com/apps); czy rejestracja aplikacji dostawcy kuriera jest samoobsługowa; czy HubRise ma realnych klientów-restauracje w Polsce?

## 🔴 SILNIKI ZAMÓWIEŃ / POS PL — cele bezpośrednie

6. **[UpMenu API]** Gdzie jest aktualna dokumentacja REST API UpMenu (stary URL /rest-api/ = 404)? Czy są webhooki `order.created` z pełnym adresem dostawy i telefonem klienta? Czy jest sandbox? Jak formalnie zostać dostawcą delivery (jak Stava/Glovo On-Demand/Uber Direct/Shipday)?
7. **[GoPOS]** Czy GoPOS REST API (gopos.pl/integrators) ma webhooki push (nowe zamówienie/zmiana statusu) i sandbox? Czy program integratora dopuszcza dodanie WŁASNEGO dostawcy kuriera obok natywnych Wolt Drive/Stava/DeliGoo, czy slot jest komercyjnie zamknięty? Koszt i czas certyfikacji?
8. **[Dotykačka]** Czy REST API v2 (api.dotykacka.cz) ma webhooki (push), czy tylko polling? Czy przez API da się wpiąć zewnętrznego kuriera w obieg zlecenia (zapis statusu dostawy), czy tylko odczyt zamówień?
9. **[POSbistro]** Czy POSbistro ma publiczną dokumentację API / webhooki / sandbox (kontakt partners@posbistro.com)? Czy dopuszczają zewnętrznego dostawcę kuriera bezpośrednio, czy wyłącznie przez Restimo?
10. **[Papu.io]** Czy Papu.io umożliwia wysłanie zlecenia do ZEWNĘTRZNEGO dostawcy kuriera (dispatch-out), czy tylko wsysa zamówienia (integr-in) do własnego dispatchu? Ile restauracji obsługuje i w których miastach? *(konkurencyjnie krytyczne)*
11. **[Zamów.online]** Czy mają własne API/webhooki dla dostawcy kuriera, czy wszystko idzie przez Restimo?
12. **[iPOS]** Czy „iPOS" to realny system POS gastro w Polsce, czy pomyłka nazwy (iPOS-terminale płatnicze / RePOS / nomee / ABS POS)? Zidentyfikować właściwego gracza i jego skalę.
13. **[Storyous/Teya]** Czy API Teya (developer.teya.com) obejmuje zamówienia/dispatch dla Storyous POS, czy tylko płatności? Czy jest slot na zewnętrznego dostawcę dostawy?
14. **[Poster w PL]** Jaki jest realny udział Poster POS w Polsce (i w Białymstoku)? Czy warto w direct-integrację mimo świetnego API?
15. **[Liczby instalacji]** Twarde liczby restauracji per system w PL: GoPOS, POSbistro, Restaumatic (5000?), UpMenu, Papu.io, Dotykačka, LSI, SOGA, S4H — do priorytetyzacji.

## 🟡 POS GLOBALNE

16. **[SumUp POS w PL]** Czy SumUp POS Pro (ex-Tiller) lub Goodtill jest realnie sprzedawany/lokalizowany dla gastronomii w Polsce, czy tylko terminale płatnicze? Ilu klientów gastro PL?
17. **[Oracle OPN]** Realny koszt członkostwa Oracle PartnerNetwork i czas walidacji integracji z Simphony (Transaction Services Gen2); czy w Oracle Restaurants Marketplace istnieje kategoria „delivery dispatch / last-mile carrier"?
18. **[Revel w PL]** Czy biuro Revel Systems w Polsce oznacza lokalną sprzedaż POS restauracjom, czy tylko R&D/support? Baza klientów gastro PL? Dokumentacja dev/webhooki/sandbox?
19. **[Lightspeed payload]** Czy event/webhook zamówienia w Lightspeed K-Series zawiera adres dostawy i telefon klienta końcowego (dane niezbędne dla dyspozytorki kuriera)? Jakie scope są wymagane?
20. **[UI-hook „wyślij kuriera"]** Czy w Lightspeed / SumUp POS / Oracle Simphony istnieje mechanizm przycisku/akcji w UI POS wywołującej zewnętrznego dostawcę dostawy z ekranu zamówienia (jak Toast Delivery Services), czy jedynie integracja webhook/panel zewnętrzny?
21. **[NCR Aloha PL]** Czy NCR Voyix/Aloha ma jakąkolwiek obecność w PL przez resellerów — czy w ogóle warto?

## 🟡 AGREGATORY

22. **[Glovo self-delivery PL]** Czy Glovo w Polsce pozwala restauracji na 100% własną dostawę (bez kurierów Glovo)? Na jakich warunkach prowizyjnych?
23. **[Pyszne.pl stawki + transmisja]** Aktualne stawki prowizji Pyszne.pl: self-delivery vs kurierzy Pyszne (weryfikacja widełek 13-15% vs ~30%). Czy zamówienia self-delivery da się wyprowadzić w czasie rzeczywistym do zewnętrznego dispatchu przez Deliverect/Restimo?
24. **[Bolt Food PL]** Monitoring: czy pojawiają się sygnały wycofania Bolt Food z Polski (po wyjściu z Chorwacji 10.2025)?

## 🟡 KONKURENCI (benchmark handlowy; techniczny = FAZA 2)

25. **[Stava]** Ile realnie oddziałów ma Stava w 2026 (rozbieżność 23 vs 62), w których miastach, czy pokrywa Białystok? Model cenowy dla restauracji?
26. **[DeliGoo]** Miasta obsługiwane przez DeliGoo w 2026 (czy Białystok?); model rozliczeń; szczegóły API (apidoc.deligoo.pl) — jak wygląda ich kontrakt integracyjny?
27. **[Cennik konkurencji]** Stawki Wolt Drive / Uber Direct / Glovo On-Demand / Stuart w PL dla restauracji (za dostawę, strefy, minimum) — nasz benchmark cenowy.
28. **[XDELIVER]** Czy x-delivery.io działa w Polsce w segmencie dostaw gastro? Skala i model (SaaS vs operator)?
29. **[Deliverky]** Czym dokładnie jest deliverky.com/pl i jaka jest jego skala?
30. **[Trasado]** Czy istnieje firma/produkt „Trasado" (PL, kurierka/gastro)? Podejrzenie: nie istnieje / błędna nazwa.

## ⚪ POZOSTAŁE

31. **[Sieci pizzy]** Jak Telepizza/Pizza Hut PL i Biesiadowo realizują dostawy (własna flota / DeliGoo / Stava / agregatory)?
32. **[PayEye]** Czy PayEye oferuje moduł zamówień gastro poza płatnością biometryczną? (prawdopodobnie nie)
33. **[Payloady webhooków]** Dla każdego systemu oznaczonego [DW] w kolumnie „webhooki" mapy rynku: potwierdzić w dokumentacji, że payload zamówienia zawiera adres dostawy + telefon klienta końcowego.

## 🔴 PO FAZIE 2 — benchmark liderów

34. **[Uber Direct w PL — KRYTYCZNE]** Czy Uber Direct (courier-as-a-service, nie Uber Eats) jest dostępny dla restauracji/merchantów w Polsce w 2026? Uber Eats działa w PL, ale dostępność produktu Direct wymaga potwierdzenia (Shopify-app tylko US/CA/FR).
35. **[Cenniki PL liderów]** Stawki Wolt Drive i Glovo LaaS w Polsce (za dostawę: baza + km/strefa; model faktury) — za bramką sprzedażową; kontakt handlowy lub źródła rynkowe.
36. **[Glovo LaaS webhooki]** Pełna lista zdarzeń webhook LaaS, struktura payloadu, podpis, retry — publiczny OpenAPI ich nie listuje (największa luka dokumentacyjna benchmarku).
37. **[Wolt Drive szczegóły]** Pełny enum pola `status` odpowiedzi delivery; czy środowisko testowe symuluje przejścia statusów/pozycję kuriera; czas onboardingu w PL i wymogi umowy/wolumenu; limity fizyczne PL (dystans/waga/godziny/miasta).
38. **[Uber Direct szczegóły]** TTL quote w classic `delivery_quotes` (estimate=15 min — czy classic też); polityka retry webhooków (3 vs 7 prób — która generacja); czy create deduplikuje po `external_id`; potwierdzenie braku COD; polityka opłat anulacji.
39. **[DoorDash szczegóły]** (tylko jako wzorzec API) Pełna lista `event_name` webhooków; czy nowy Drive dodał podpis HMAC (dziś Basic/OAuth na endpoincie); TTL quote (~5 min?).
40. **[Stuart szczegóły]** Pełny enum statusów, dokładne ścieżki pricing/validate/cancel, katalog eventów webhook + podpis/retry, POD, wartości package/transport types dla PL → najprościej: założyć darmowe konto sandbox (dashboard.sandbox.stuart.com, ~15 min) i pobrać OpenAPI/Postman.
41. **[Deligoo szczegóły]** Stawki cennika; okna/opłaty anulowania; czy POD obejmuje zdjęcie/PIN (dziś tylko finished_at+lat/lng); katalog kodów błędów; czy webhooki mają retry.
42. **[Deliverect Dispatch auth]** Jak Deliverect uwierzytelnia webhooki do dostawcy (HMAC/podpis vs sekret w URL) + struktura endpointu availability (`/reference/validate-delivery` za 403).
43. **[Deliveroo Signature]** (niski priorytet) Szczegóły API Signature (auth, statusy, sandbox) — za loginem partnerskim; tylko jeśli rozważymy UK/IE.
44. **[Open Delivery LOGISTICS]** Wyciąg enumów statusów i struktur modułu LOGISTICS z `abrasel-nacional.github.io/docs/openapi.yaml` — jako referencja nazewnictwa przed projektem naszego API.

---
*Aktualizacja: po każdej fazie dopisujemy nowe [DW]; po otrzymaniu odpowiedzi przenosimy fakt do właściwego artefaktu ze statusem [ZWERYFIKOWANE] i skreślamy pytanie tutaj.*
