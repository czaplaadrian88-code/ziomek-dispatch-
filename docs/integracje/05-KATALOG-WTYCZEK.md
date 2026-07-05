# 05 — KATALOG WTYCZEK (budujemy sami lub przez middleware)

> Wielki Audyt Integracji — FAZA 4A. Data: 2026-07-05.
> Zakres: systemy `SAMI` + osiągalne przez `MIDDLEWARE` z `01-MAPA-RYNKU.md`. Wszystkie wtyczki zakładają gotowy pakiet **Integration Readiness v1** (`03-ANALIZA-LUK.md`) — bez niego żadna karta nie startuje.
> Nakład w osobotygodniach (ot) — sam konektor, bez IR v1. Kierunek „dwukierunkowa" = oni wysyłają zamówienie → my zwracamy statusy + tracking (+ GPS).

## Przegląd katalogu

| # | Wtyczka | System docelowy | Ścieżka | Trudność | Nakład | Self-install przez restaurację? | Zgoda dostawcy? |
|---|---|---|---|---|---|---|---|
| W1 | Konektor **Deliverect Dispatch** | Deliverect (→ setki POS globalnie) | MIDDLEWARE/PARTNER-OPEN | Medium | 3–4 ot | TAK (włącza nas w panelu Deliverect) | TAK — rejestracja partnera + certyfikacja (formalna, otwarta) |
| W2 | Konektor **Restimo** | Restimo (→ GoPOS, POSbistro, Dotykačka, SOGA, S4H, LSI, nomee + 5 agregatorów) | MIDDLEWARE/PARTNER | Medium | 3–5 ot [kontrakt DW] | TAK (kafelek kuriera w Restimo) | TAK — program partnerski (warunki do potwierdzenia) |
| W3 | Wtyczka **WooCommerce** | WordPress/WooCommerce | SAMI | Low-Medium | 3–4 ot | **TAK** (instalacja z wordpress.org) | NIE (review repozytorium — lekki) |
| W4 | Aplikacja **Poster POS** | Poster (dev.joinposter.com) | SAMI | Low-Medium | 2–3 ot | TAK (katalog aplikacji Poster) | NIE (samoobsługowa rejestracja aplikacji) |
| W5 | Konektor **Dotykačka** | Dotykačka API v2 | SAMI | Medium | 3–4 ot | CZĘŚCIOWO (aktywacja z naszym wsparciem) | NIE dla API (klucz testowy samoobsługowo); listing oficjalny — kontakt |
| W6 | Aplikacja **Shopify** | Shopify App Store | PARTNER (review) | Medium | 4–6 ot | TAK (App Store) | TAK — review Shopify (OAuth, wymogi bezpieczeństwa) |
| W7 | Moduł **PrestaShop** | PrestaShop Addons | PARTNER (walidacja) | Medium | 3–4 ot | TAK (Addons) | TAK — walidacja Addons |
| W8 | Moduł **Make (+ Zapier)** | iPaaS | SAMI | Low | 1–2 ot | TAK (scenariusze publiczne) | lekki review przy publicznej aplikacji |
| W9 | Konektor **Lightspeed K-Series** | Lightspeed | PARTNER (lekki) | Medium-High | 4–6 ot | CZĘŚCIOWO | TAK — akceptacja API client (ręczna, lekka) |
| W10 | **Uniwersalny „DIY kit"** | dowolny system z devem | SAMI | Low | 1–2 ot (na bazie IR v1) | TAK | NIE |

Suma „quick-win path" (W1+W2+W3+W8+W10): ~11–16 ot. Pełny katalog: ~27–38 ot.

---

## KARTY WTYCZEK

### W1 — Konektor Deliverect Dispatch ⭐ (najwyższa dźwignia globalna)
- **Kierunek:** dwukierunkowa (oni: Validate/Create/Cancel → my; my: Update Delivery/Cancel → oni).
- **Kluczowe funkcje:** nasz kurier jako opcja dispatch w panelu Deliverect (obok Wolt Drive); auto-wycena (canDeliver+cena+ETA) na każde zamówienie; statusy i kurier w ich UI.
- **Endpointy/zdarzenia (z benchmarku, [ZW]):** MY wystawiamy webhooki `POST /validate` (odpowiedź: `canDeliver`, `jobId`, `pickupTimeETA`, `distance`, `deliveryLocations[]`, `price{price,taxRate}`, `currency`), `POST /create`, `POST /cancel(reason)`; MY wołamy ich `Update Delivery` (statusy `COURIER_ASSIGNED→PICKED_UP→DELIVERED`+`CANCELLED`, czasy, kurier, ETA) i `Cancel`. Wymóg: standaryzowane URL-e (nie per-klient). Mapowanie: ich Validate = nasz `/v1/quotes`; ich Create = nasz `/v1/deliveries`.
- **Trudność: Medium** (kontrakt jawny; certyfikacja przez Delivery Manager App). **Nakład: 3–4 ot.**
- **Marketplace/self-install:** tak — restauracja włącza nas w Deliverect bez naszego udziału (to jest cała wartość).
- **Zgoda:** rejestracja become-a-partner + certyfikacja — formalna, ale program otwarty [koszt DW].
- **Ryzyka:** Wolt Drive już tam jest (konkurencja w tym samym kafelku — wygrywamy ceną/pokryciem lokalnym); zasięg Deliverect w PL dopiero rośnie; model komercyjny partnera nieznany [DW]; nasz zasięg geograficzny (Białystok) ogranicza atrakcyjność — konektor ważny STRATEGICZNIE (ekspansja), nie na pilota.

### W2 — Konektor Restimo ⭐ (najwyższa dźwignia PL)
- **Kierunek:** dwukierunkowa (Restimo routuje zamówienie z agregatorów/POS do nas; my zwracamy przyjęcie, ETA, statusy, tracking).
- **Kluczowe funkcje:** nasz kurier jako opcja „courier service" w tablecie/panelu Restimo obok Wolt Drive/Uber Direct/Stava/DeliGoo — jedna integracja otwiera GoPOS, POSbistro, Dotykačkę, SOGA, S4H, LSI, nomee i 5 agregatorów.
- **Endpointy/zdarzenia:** kontrakt niepubliczny [DW — pytanie #1 briefu]. Zakładany kształt (wzorzec Deligoo, który tam jest): oni wołają nasze quote+create; my pushujemy statusy webhookiem. Nasza strona = IR v1 bez zmian.
- **Trudność: Medium** (technicznie; handlowo zależne od ich warunków). **Nakład: 3–5 ot** [DW].
- **Self-install:** TAK — restauracja wybiera nas w Restimo.
- **Zgoda:** TAK — program partnerski (hello@restimo.com; docs.restimo.com; /become-a-partner).
- **Ryzyka:** Restimo kontroluje dostęp do popytu (może preferować dostawców z lepszą prowizją dla nich); konkurenci już wpięci (DeliGoo, Stava); warunki komercyjne nieznane; ryzyko „pay-to-play".

### W3 — Wtyczka WooCommerce („[marka] — kurier dla restauracji")
- **Kierunek:** dwukierunkowa (hook `woocommerce_order_status_processing` → nasz create; my → webhook statusów → meta zamówienia + mail/SMS z tracking_url).
- **Kluczowe funkcje:** przy zamówieniu z dostawą auto-zlecenie kuriera (lub przycisk „Wyślij kuriera" w adminie zamówienia); wycena/ETA w koszyku (nasze `/v1/quotes` + `delivery-areas`); link śledzenia dla klienta; COD przekazywany kurierowi.
- **Endpointy/zdarzenia:** ich strona — hooki zamówień Woo + strona ustawień (klucz API, lokal, tryby); nasza strona — `/v1/quotes`, `/v1/deliveries`, webhooki statusów (wtyczka rejestruje endpoint odbiorczy w WP REST).
- **Trudność: Low-Medium** (wzorzec: Shipday for WooCommerce — skopiować UX). **Nakład: 3–4 ot** (+utrzymanie zgodności wersji WP/Woo).
- **Marketplace/self-install:** TAK — wordpress.org/plugins, darmowa, restauracja instaluje sama i wkleja klucz z naszego panelu.
- **Zgoda:** NIE (lekki review repo).
- **Ryzyka:** długi ogon supportu (dziwne motywy/wtyczki kolidujące); mała-średnia baza gastro-Woo w PL (ale to najtańszy w pełni własny kanał i demo dla partnerów: „tak wygląda nasza integracja").

### W4 — Aplikacja Poster POS
- **Kierunek:** dwukierunkowa (webhook zamówienia Postera → nasz create; nasz status → aplikacja w POS).
- **Kluczowe funkcje:** przycisk/aplikacja w interfejsie POS (Poster wspiera **embed aplikacji w POS**) „Zamów kuriera"; auto-przekazanie adresu/telefonu; status w POS.
- **Endpointy/zdarzenia:** ich strona — dev.joinposter.com API + subskrypcja webhooków (zamówienia/statusy) + rejestracja aplikacji w katalogu (samoobsługowa); nasza — IR v1.
- **Trudność: Low-Medium.** **Nakład: 2–3 ot.**
- **Self-install:** TAK (katalog aplikacji Poster).
- **Zgoda:** NIE.
- **Ryzyka:** mały udział Poster w PL/Białystok [DW #14] — ROI niepewny; traktować jako tani „drugi dowód" otwartego modelu po Woo.

### W5 — Konektor Dotykačka
- **Kierunek:** dwukierunkowa (odczyt zamówień API v2; status naszej dostawy zapisywany zwrotnie [DW czy API pozwala — pytanie #8]).
- **Kluczowe funkcje:** „wyślij kuriera" dla zamówień z Dotykački; auto-dane klienta; statusy.
- **Endpointy/zdarzenia:** api.dotykacka.cz v2 (REST, klucz testowy + testowa chmura); webhooki [DW] — możliwy polling.
- **Trudność: Medium** (jeśli brak webhooków → polling). **Nakład: 3–4 ot.**
- **Self-install:** częściowo (parowanie kont z naszym wsparciem); oficjalny listing = kontakt z Dotykačką.
- **Zgoda:** NIE dla developmentu (otwarte API); TAK dla oficjalnego listingu.
- **Ryzyka:** średni udział PL; konkurencja ma tam natywnego Wolta; wersje API.

### W6 — Aplikacja Shopify
- **Kierunek:** dwukierunkowa (webhook `orders/create` → quote/create; statusy → fulfillment events + tracking_url).
- **Wzorzec:** QuickShipper (stawki kurierów przy checkout) + Shipday. **Trudność: Medium** (review App Store, OAuth, billing API). **Nakład: 4–6 ot.**
- **Self-install:** TAK (App Store). **Zgoda:** review Shopify.
- **Ryzyka:** mała baza gastro-Shopify PL → niski priorytet; robić dopiero przy ekspansji/e-com (paczki!). Tor paczkowy zwiększa sens tej wtyczki (nie tylko gastro).

### W7 — Moduł PrestaShop
- **Kierunek:** dwukierunkowa (hook zamówienia → create; statusy → historia zamówienia).
- **Wzorzec:** XpressDelivery. **Trudność: Medium** (walidacja Addons). **Nakład: 3–4 ot.**
- **Self-install:** TAK (Addons). **Zgoda:** walidacja Addons (płatny listing).
- **Ryzyka:** gastro na Presta rzadkie; sens głównie dla toru paczkowego (e-com lokalny Białystok). Niski priorytet.

### W8 — Moduł Make (+ Zapier) i „generic webhook"
- **Kierunek:** dwukierunkowa (scenariusz: dowolny trigger → moduł „Create Delivery"; nasz webhook → dowolna akcja).
- **Kluczowe funkcje:** klej dla egzotycznych systemów (formularz www, arkusz, mail-parser, CRM); gotowe szablony scenariuszy („Nowy wiersz w arkuszu → kurier").
- **Endpointy:** nasza strona — IR v1 (moduł Make to cienki opis naszego API + auth). **Trudność: Low.** **Nakład: 1–2 ot.**
- **Self-install:** TAK. **Zgoda:** lekki review przy publikacji publicznej aplikacji Make.
- **Ryzyka:** niski wolumen per klient; za to zerowy koszt utrzymania i natychmiastowy „fallback dla BRAK" (patrz 06 §4C).

### W9 — Konektor Lightspeed K-Series
- **Kierunek:** dwukierunkowa (webhook zamówienia → create; statusy → API/notatka zamówienia).
- **Endpointy:** api-portal.lsk.lightspeed.app (REST, webhooki, sandbox); wymaga zatwierdzenia API client (kseries.api@lightspeedhq.com) [DW payload z adresem+telefonem — pytanie #19].
- **Trudność: Medium-High** (proces partnerski + nieznany payload). **Nakład: 4–6 ot.**
- **Self-install:** częściowo (Partner integrations listing po akceptacji).
- **Ryzyka:** udział Lightspeed w PL gastro umiarkowany; sens przy klientach premium/sieciowych i ekspansji EU.

### W10 — Uniwersalny „DIY kit" (nasz własny produkt)
- **Co to:** publiczne API + gotowce obniżające próg dla restauracji z własnym devem/stroną: snippet JS „przycisk Zamów kuriera" (widget wyceny+zlecenia), przykłady curl/PHP/Node, kolekcja Postman, przykładowy odbiornik webhooków.
- **Kierunek:** dwukierunkowa. **Trudność: Low** (opakowanie IR v1). **Nakład: 1–2 ot.**
- **Self-install:** TAK. **Zgoda:** NIE.
- **Ryzyka:** brak — to nasza dokumentacja+DX; warunek wiarygodności przy rozmowach partnerskich (pokazujemy działające API, sandbox i widget).

---

## Uwagi przekrojowe

1. **Każda karta to cienki adapter nad IR v1** — żadna wtyczka nie ma własnej logiki dyspozytorskiej; wszystkie mówią do `/v1/quotes|deliveries` i słuchają tych samych webhooków (architektura 04 §2).
2. **Kolejność budowy wg ROI dla pilota Białystok:** W10 (DIY kit) → W3 (Woo — demo publiczne) → W2 (Restimo — po odpowiedzi partnerskiej) → W4/W5 (Poster/Dotykačka — tanie dowody) → W1 (Deliverect — strategicznie, przy ekspansji) → W9/W6/W7 (przy popycie).
3. **GoPOS, POSbistro, Restaumatic, UpMenu NIE mają kart** — to ścieżki `PARTNER` (kontrakt po ich stronie, my dostarczamy IR v1 + ew. `translate()`); patrz `06-PARTNERSTWA-I-KONTAKTY.md`.
4. Utrzymanie: budżetować **~0,5–1 ot/kwartał na wtyczkę** (zmiany wersji platform, support) — wchodzi do kosztu utrzymania per integracja w roadmapie (07).
