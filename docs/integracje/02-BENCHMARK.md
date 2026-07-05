# 02 — BENCHMARK liderów delivery-as-a-service

> Wielki Audyt Integracji — FAZA 2. Data: 2026-07-05.
> Zakres: Wolt Drive, Glovo On-Demand (LaaS), Uber Direct, DoorDash Drive, Stuart, Deligoo (+ Stava jako model biznesowy, + Deliveroo Signature jako dowód kategorii) + otwarte standardy.
> Statusy: **[ZW]** = zweryfikowane w oficjalnej dokumentacji (URL-e w sekcjach) · **[DW]** = do weryfikacji (pytania w `99-RESEARCH-BRIEF.md`) · **[HIP]** = hipoteza. Nazwy endpointów/pól w oryginale.

## TL;DR

1. **Branżowy wzorzec jest konwergentny i jednoznaczny:** `quote/promesa (cena+ETA+TTL) → create (z quote_id + klucz idempotencji) → webhooki statusów + GPS kuriera → tracking_url white-label → POD`. Wszyscy liderzy robią to samo, różnią się nazwami.
2. **Najlepsze wzorce do skopiowania:** webhooki Wolta (14 zdarzeń, podpis JWT HS256, retry z konfigurowalnym backoffem, GPS pushem), idempotencja DoorDasha (`external_delivery_id`), POD Ubera (bloki `*_verification`), sandbox-symulator DoorDasha/Glovo (przewijanie stanów bez kuriera), API Deligoo (polskie realia: COD, `pickup_at`, kwoty w groszach, statusy dwuwarstwowe).
3. **Deligoo obsługuje Białystok** i ma pełne publiczne API — to nasz bezpośredni benchmark rynkowy i techniczny. Stava też jest w Białymstoku (bez API, tylko przez POS-y). Stuart w Białymstoku raczej nie.
4. **Deliverect Dispatch** publikuje dokładną specyfikację webhooków, które MY musielibyśmy wystawić (Validate/Create/Cancel + Update) — to gotowy kontrakt konektora „bądź dostawcą dla huba".
5. Jedyny otwarty standard branżowy to **Open Delivery (Abrasel, Brazylia)** z modułem LOGISTICS — dobra referencja nazewnictwa; poza nim standard = de-facto wzorzec Uber/DoorDash/Wolt.
6. DoorDash Drive w PL nie działa (wzorzec API only); **dostępność Uber Direct w PL wymaga weryfikacji** (krytyczne pytanie w briefie).

---

## 1. WOLT DRIVE [ZW developer.wolt.com/docs/wolt-drive]

- **Auth:** statyczny Bearer „merchant key", jeden per merchant, bez expiry (prostota kosztem bezpieczeństwa); token test+prod wydaje account manager.
- **Flow:** dwa tryby — **Venueful** i **Venueless** (dynamiczny pickup). Promesa: `POST /v1/venues/{venue_id}/shipment-promises` → `id`, `valid_until`, **`is_binding`** (non-binding = nie utworzysz dostawy), `price`, `eta_minutes`; venueless `POST /merchants/{id}/delivery-fee`. Create: `POST .../deliveries` z `shipment_promise_id`, `recipient{name,phone_number}`, `parcels[]`, `cash` (**COD!**), `handshake_delivery` (PIN), `min_preparation_time_minutes` (≤60), `contents[].id_check_required`. Odpowiedź: `status=INFO_RECEIVED`, `tracking.url`, `wolt_order_reference_id`, ETA pickup+dropoff. Cancel: `PATCH /order/{id}/status/cancel` + `reason`; do akceptacji kuriera, później support/opłata. Strefy: `GET /merchants/{id}/delivery-areas` (poligony).
- **Webhooki (wzorzec-złoto):** payload jako **JWT HS256** z `client_secret`; retry **exponential backoff konfigurowalny** (`exponent_base`, `max_retry_count`); **14 zdarzeń**: `order.received/rejected/pickup_eta_updated/pickup_started/picked_up/pickup_arrival/dropoff_started/dropoff_arrival/dropoff_completed/delivered/dropoff_eta_updated/handshake_delivery` + opc. `order.customer_no_show`, **`order.location_updated` (GPS kuriera PUSH)**.
- **Błędy/idempotencja:** `{error_code, reason, details}`; 422 z `loc/msg` per pole; 429/5xx z zasadami retry. Dedup przez `merchant_order_reference_id` → `DUPLICATE_ORDER` (bez osobnego nagłówka).
- **Sandbox:** środowisko dev (test venue od AM); symulator kuriera [DW].
- **Onboarding:** nie self-service (kontakt z zespołem) [DW czas/umowa]. **Rozliczenia:** cena per dostawa w promesie; cennik PL [DW].
- **Ekosystem:** **50+ partnerów integracyjnych** (developer.wolt.com/integration-partners: Deliverect, HubRise, **Restimo**, SIDES, Smoothr, Qopla…) — model „wybierz swój POS" zamiast „koduj".

## 2. GLOVO ON-DEMAND / LaaS [ZW logistics-docs.glovoapp.com/laas-partners]

⚠ Nie mylić z Partner/Order API (POS dla marketplace). LaaS = odpowiednik Wolt Drive.
- **Auth:** **OAuth2 client_credentials** (`POST /oauth/token`), token JWT ~10 min [DW TTL], refresh token — najbardziej „enterprise" w stawce.
- **Flow:** `POST /laas/parcels/validation` → `EXECUTABLE` / błędy stref i czasu (⚠ twarde: pickup i dropoff w tym samym `cityCode`); `GET /laas/working-areas`. Create: `POST /laas/parcels` (`address{rawAddress…lat,lng}`, `contact{name,phone,email}`, `packageDetails`, `packageId`, `price`) → `trackingNumber`, `status`, `cancellable`, `price`. Content types: `FOOD`, `FOOD_WL`, `GENERIC_PARCEL`. Tracking: `GET /laas/parcels/{tn}` + `/status` + `/history` + `parcel_tracking_links/{tn}` + `/courier-contact` + **`/courier-position` (GPS pull)**. Cancel: `POST /laas/parcels/{tn}/cancel` (pole `cancellable`). **COD rozdzielony: „Delivery Value" + „Parcel Value"** (dwie kwoty pobrania).
- **Webhooki:** LUKA dokumentacyjna — OpenAPI nie listuje zdarzeń [DW]; fallback polling `/status` + `/history`.
- **Statusy:** przeładowane (3 wymiary: Parcel/Status/Location States z artefaktami q-commerce — biny, WH); uproszczony cykl: `RECEIVED → READY_FOR_PICKUP → DISPATCHED → CANCELED`.
- **Sandbox (najmocniejszy punkt):** staging + **`GET /laas/parcels/{tn}/simulate/successful-attempt` i `/simulate/exhausted-attempt`** — przewijanie pełnego cyklu bez kuriera.
- **Onboarding:** rejestracja → umowa → integracja → stage → prod; nie self-service [DW czas PL]. **Rozliczenia** [DW].
- **Ekosystem:** wtyczka WooCommerce, Laravel SDK; mniejszy/mniej scentralizowany niż Wolt [DW katalog].

## 3. UBER DIRECT [ZW developer.uber.com/docs/deliveries]

⚠ Dwie generacje API: **classic v1** (`customer_id`, statusy lowercase) i nowsza „dapi" (statusy UPPERCASE, `*_verification`). Wzorować się na classic + POD z nowej.
- **Auth:** OAuth2 client_credentials (`POST auth.uber.com/oauth/v2/token`, scope `eats.deliveries`); Customer ID w ścieżce.
- **Flow:** Quote: `POST /v1/customers/{customer_id}/delivery_quotes` → `id`, **`expires` (TTL; w gen. estimate = 15 min)**, `fee`, `currency`, `dropoff_eta`. Create: `POST .../deliveries` (`quote_id`, pickup/dropoff address+name+phone+coords, `manifest_items[]`) + opc. `deliverable_action` (meet_at_door/leave_at_door), `tip`, `requires_id`, `signature_requirement`, `pincode`, `undeliverable_action`, `external_id` → `id (del_)`, `status`, **`tracking_url`**, `courier`, `related_deliveries`. Cancel: `POST .../deliveries/{id}/cancel` [DW opłaty]. **Zwrot: auto-tworzona dostawa `ret_` + `related_deliveries`.** **COD: BRAK natywnego pola cash** [DW].
- **Webhooki:** `event.delivery_status` + **`event.courier_update` (GPS co ~20 s)** + `event.refund_request`; payload z **`event_id` (dedup)**, `resource_href`, `meta{external_order_id, status, courier_trip_id, is_returning}`; podpis **HMAC-SHA256 w `x-postmates-signature`/`x-uber-signature`**; retry backoff (3 vs 7 prób — rozbieżność generacji [DW]); ACK=200.
- **Statusy:** classic `pending→pickup→pickup_complete→dropoff→delivered` (+canceled/returned); nowa gen `SCHEDULED/EN_ROUTE_TO_PICKUP/ARRIVED_AT_PICKUP/EN_ROUTE_TO_DROPOFF/ARRIVED_AT_DROPOFF/COMPLETED/FAILED`.
- **Sandbox:** **Robocourier** — symulowany kurier przechodzi statusy automatycznie.
- **POD (najbogatszy model):** bloki `pickup_verification`/`dropoff_verification`/`return_verification`: `signature_requirement{enabled, collect_signer_name…}` → `signature_proof.image_url`; `picture:true` → `image_url`; `pincode`; `identification{min_age}`; `barcodes[]` → `scan_result`. Zdjęcia w API ~30 dni.
- **Onboarding:** self-service dashboard (direct.uber.com) + umowa dla wolumenu. **Rozliczenia:** flat fee per delivery; benchmark Toast: **6,99 USD/order**. ⚠ **Dostępność w PL [DW — krytyczne]** (Uber Eats wyszedł z PL).
- **Ekosystem:** Toast (domyślny od XII 2024), Shopify (oficjalna app US/CA/FR), WooCommerce plugin, Olo.

## 4. DOORDASH DRIVE v2 [ZW developer.doordash.com; PL: NIE DZIAŁA — wzorzec API only]

- **Auth:** własnoręcznie budowany **JWT HS256** (`developer_id`+`key_id`+`signing_secret`, header `dd-ver: DD-JWT-V1`, exp ~5 min); osobne klucze Sandbox/Prod.
- **Flow:** Quote: `POST /drive/v2/quotes` (**`external_delivery_id` = klucz idempotencji**, adresy+kontakty, `order_value` w centach, `tip`, `contactless_dropoff`, `action_if_undeliverable`) → `fee`, czasy estimated ± bounds. Accept: `POST /drive/v2/quotes/{id}/accept`. Create bez quote: `POST /drive/v2/deliveries` (+ `items[]`). Tracking: `GET /drive/v2/deliveries/{id}` → `delivery_status`, `dasher_location`, czasy actual/estimated, `tracking_url`. Cancel: `PUT .../cancel` — **niemożliwe po przypisaniu Dashera**. Return: return delivery.
- **Webhooki:** `DASHER_CONFIRMED`, `DASHER_CONFIRMED_PICKUP_ARRIVAL`, `DASHER_PICKED_UP`, `DASHER_CONFIRMED_DROPOFF_ARRIVAL`, `DASHER_DROPPED_OFF`, `DELIVERY_CANCELLED` [DW pełna lista]; auth webhooka = **Basic/OAuth na naszym endpoincie** (nie HMAC) [DW]; fallback polling.
- **Statusy:** `quote → created → confirmed → enroute_to_pickup → arrived_at_pickup → picked_up → enroute_to_dropoff → arrived_at_dropoff → delivered` (+`cancelled`/`returned`); ⚠ może wracać do `created` (Dasher się odpina) — dobra lekcja modelowania.
- **Idempotencja (najczystsza w stawce):** `external_delivery_id` — retry z tymi samymi danymi → zwraca istniejącą dostawę; ten sam id + inne dane → odrzut.
- **Sandbox:** pełny + **Delivery Simulator** („Advance to Next Step" przewija stany, webhooki lecą) — najlepszy DX w stawce.
- **POD:** `dropoff_requires_signature` → `dropoff_signature_image_url`; zdjęcie zawsze; `pin_code_type = customer_phone_number|merchant_provided_number`; age/ID.
- **Onboarding:** self-serve tylko restauracje; AM; przez partnera (Olo/Toast/Square). **Rozliczenia:** flat fee bez subskrypcji, `fee_components` (`distance_based_fee`), faktura miesięczna; benchmark **7,49 USD/order**.

## 5. STUART [ZW api-docs.stuart.com; PL: 7 miast, Białystok raczej NIE]

- **Auth:** OAuth2 client_credentials, token 30 dni; **sandbox self-service** (dashboard.sandbox.stuart.com + testowa karta + Postman collection) — najniższy próg wejścia dla dewelopera.
- **Flow:** pricing (opc.) → validation (opc.) → `POST /v2/jobs` (`transport_type` bike/motorbike/cargobike/e-vehicle/car/van; `pickups[]`, `dropoffs[]` z `package_type`, **`client_reference` (idempotencja)**, `end_customer_time_window`). Tracking: `tracking_url`, pozycja **co 10–30 s**, zanonimizowany telefon kuriera.
- **Webhooki:** v3 (v2 deprecated — **wersjonowanie payloadów!**); katalog eventów/podpis [DW].
- **Statusy** [DW pełny enum]: `new/searching → in_progress/picking → delivering → delivered` + `canceled/expired`.
- **Rozliczenia:** per job wg dystansu/transportu [DW cennik PL]. **Integracje:** Flipdish; SDK PHP/Java/JS/C#.

## 6. DELIGOO [ZW apidoc.deligoo.pl; **Białystok TAK** — 28 miast, 12:00–22:00]

Nasz bezpośredni bliźniak rynkowy z pełnym publicznym API:
- **Auth:** Bearer per partner (`POST /api/partners_app/v1/sign_in`); sandbox = **preproduction-app.deligoo.pl**.
- **Flow:** `POST .../addresses/calculate_distance` (+ `city/street_suggestions`, `find_postal_code`); create `POST /api/partners_app/v1/orders` (lub `/clients/<ID>/orders`): wymagane **`price_subunit` (grosze)**, **`pickup_at` (ISO8601 — czasówka!)**, **`payment_form` (paid/card/**cash** = COD natywnie)**, `delivery_method`, `service_type` (express/sameday); opc. `external_id` (**idempotencja**), `packages` (big-pizza, standard-dish, own-packaging…), `webhook_url`. Cancel `PUT .../orders/<ID>/cancel` [DW okna/opłaty].
- **Webhooki:** eventy `orders.created`, `orders.set_as_waiting/pending`, **`orders.pickups.set_as_assigned` i `orders.deliveries.set_as_assigned` osobno**; podpis **`Signature: t=<ts>,v1=HMAC-SHA256(timestamp.body)` — wzorzec Stripe** z ochroną replay.
- **Statusy dwuwarstwowe:** zlecenie `waiting→pending→started→completed/canceled/failed` + per-punkt (pickup/delivery) `pending/assigned/started/in_progress/finished/canceled/failed` — mapuje się 1:1 na nasz model podjazdów.
- **POD minimalny:** `finished_at` + `finished_lat/lng` (spójne z naszym geofence 5b) [DW foto/PIN].
- **Onboarding:** bezkosztowo, ≤ kilka dni. **Rozliczenia:** za dostawę wg dystansu+pozycji, faktura zbiorcza, bez prowizji [DW stawki]. **Integracje:** GoPOS, UpMenu, Restaumatic, BaseLinker/RedCart (partner POS trzyma token i woła `POST /orders`).

## 7. STAVA [ZW stava.eu; **Białystok TAK**; BEZ publicznego API]

Franczyza gastro-kurierska (od 2014; 17–44 miast [DW]). Integracja WYŁĄCZNIE przez partnerów POS (GoPOS od 01.2022, Restaumatic, UpMenu, Restimo) — restauracja przypisuje dostawcę z ekranu POS. Model: płatność tylko za zrealizowane dostawy, stawka wg strefy/wolumenu, bez opłat wstępnych. Dla nas: benchmark modelu biznesowego i kanału dystrybucji, nie API.

## 8. DELIVEROO SIGNATURE [ZW api-docs.deliveroo.com; gated]

Odpowiednik Wolt Drive („request delivery via Deliveroo couriers for an order managed by your internal systems") — istnieje, ale dostęp bramkowany umową/Account Managerem; szczegóły API za loginem. Znaczenie: **dowód, że każdy liczący się gracz zbudował ten sam produkt** — nie wzorzec do kopiowania.

---

## TABELA PORÓWNAWCZA

| Wymiar | Wolt Drive | Glovo LaaS | Uber Direct | DoorDash Drive | Stuart | Deligoo |
|---|---|---|---|---|---|---|
| **Auth** | Bearer statyczny 1/merchant | OAuth2 CC, JWT ~10 min | OAuth2 CC | self-built JWT HS256 ~5 min | OAuth2 CC, 30 dni | Bearer per partner |
| **Wycena** | shipment-promise (binding/non-binding, valid_until) | validation (EXECUTABLE…) [cena DW] | quote z `expires` (≈15 min) | quote + accept (osobny krok) | pricing/validate opc. | calculate_distance |
| **Idempotencja** | merchant_order_reference_id → DUPLICATE_ORDER | packageId/orderCode [DW] | external_id [DW] | **external_delivery_id (wzorzec)** | client_reference | external_id |
| **Webhooki** | **14 zdarzeń, JWT HS256, backoff konfig.** | luka dokumentacyjna | 2 strumienie + event_id, HMAC x-signature | eventy DASHER_*, Basic/OAuth | v3 (wersjonowane) | Stripe-style HMAC t=,v1= |
| **GPS kuriera** | PUSH `order.location_updated` | PULL `/courier-position` | PUSH `courier_update` ~20 s | w GET + webhook | 10–30 s | [DW] |
| **Tracking klienta** | tracking.url | parcel_tracking_links | tracking_url | tracking_url | tracking_url | [DW] |
| **COD** | pole `cash` | **2 kwoty (Delivery+Parcel Value)** | **brak** | brak (US) | [DW] | `payment_form=cash` |
| **POD** | handshake PIN + id_check | [DW] | **bloki *_verification (podpis/foto/PIN/wiek/barcode)** | podpis+foto+PIN+wiek | [DW] | finished_at+lat/lng |
| **Sandbox** | env dev (od AM) | **simulate/* endpoints** | Robocourier | **Delivery Simulator (best DX)** | **self-service** | preproduction env |
| **Onboarding** | przez zespół | umowa+stage | self-service+umowa | self-serve (restauracje) | **self-service dev** | ≤3 dni, bezkosztowo |
| **Rozliczenia** | per dostawa [DW PL] | [DW] | flat fee (~6,99 USD Toast) | flat fee (~7,49 USD) + components | per job | za dostawę, faktura zbiorcza |
| **Ekosystem POS** | **50+ partnerów (katalog)** | Woo+Laravel SDK | Toast/Shopify/Woo/Olo | Toast/Olo/Square/Woo | Flipdish, SDK | GoPOS/UpMenu/Restaumatic |
| **PL / Białystok** | PL tak / B-stok [DW] | PL tak / [DW] | **PL [DW — krytyczne]** | **brak PL** | PL 7 miast / B-stok ~nie | **PL 28 miast / B-stok TAK** |

---

## STANDARD BRANŻOWY — minimalny zestaw funkcji naszego API

Synteza: co nasze API MUSI mieć, żeby systemy restauracyjne mogły i chciały się integrować (wszystko poniżej występuje u ≥2 liderów; „(wzorzec: X)" = skąd kopiować kształt).

### MUST (bez tego integracja nie zaistnieje)
1. **`POST /quotes`** — wycena przed zleceniem: wejście pickup+dropoff (+opc. czas gotowości), wyjście `quote_id`, `fee` (grosze, wzorzec Deligoo), `currency`, `pickup_eta`/`dropoff_eta`, **`expires_at` (TTL 5–15 min)**, flaga wiążącości (wzorzec: Wolt shipment-promise, Uber quote).
2. **`POST /deliveries`** — utworzenie z `quote_id` + **obowiązkowy `external_delivery_id` jako klucz idempotencji** (retry z tymi samymi danymi → 200 z istniejącą; ten sam id + inne dane → 409; wzorzec: DoorDash). Pola: adresy z koordami, `recipient{name, phone}`, `pickup_at` / `min_preparation_time_minutes` (czasówki — nasz twardy wymóg), `cod{amount}` (`payment_form=cash`, rozważyć rozbicie Delivery/Parcel Value wzorem Glovo), `contactless_dropoff`, `action_if_undeliverable`, `notes`.
3. **Webhooki statusowe** z: jawną listą zdarzeń pokrywającą pełny cykl (wzorzec: 14 zdarzeń Wolta), `event_id` do dedup (Uber), podpisem **HMAC-SHA256 `Signature: t=<ts>,v1=<sig>`** (wzorzec Stripe/Deligoo — prostszy niż JWT Wolta, silniejszy niż Basic DoorDasha), **retry exponential backoff** + ACK=2xx, **wersjonowaniem payloadu** (Stuart v2→v3).
4. **Kanoniczny słownik statusów (10 stanów)** — mapowalny 1:1 na wszystkich liderów:
   `CREATED → COURIER_ASSIGNED → EN_ROUTE_TO_PICKUP → AT_PICKUP → PICKED_UP → EN_ROUTE_TO_DROPOFF → AT_DROPOFF → DELIVERED` + `CANCELLED`, `FAILED/RETURNED`. (Tabela mapowania: sekcja niżej.)
5. **`tracking_url` white-label** dla klienta końcowego (mamy `/t/{token}` — zwracać w create i webhookach) + pozycja kuriera i ETA na stronie.
6. **`GET /deliveries/{id}`** — polling fallback (pełny stan + kurier + czasy estimated/actual; wzorzec DoorDash).
7. **Anulowanie** `POST /deliveries/{id}/cancel` z `reason`, jawną polityką okien (do przypisania darmowe; później opłata/odmowa) i polem `cancellable` w obiekcie (Glovo).
8. **Model błędów:** `{error_code, reason, details}` + kody biznesowe (`DROPOFF_OUTSIDE_OF_DELIVERY_AREA`, `DUPLICATE_ORDER`, `QUOTE_EXPIRED`…) + walidacja 422 per pole (wzorzec Wolt).
9. **Sandbox z symulatorem stanów** — endpointy `simulate/*` (Glovo) lub „Advance to Next Step" (DoorDash); klucze testowe self-service (Stuart).
10. **Strefy:** `GET /delivery-areas` (poligony; wzorzec Wolt) — integrator sprawdza zasięg przed ofertą.

### SHOULD (przewaga konkurencyjna, mamy zasoby)
11. **GPS kuriera PUSH** — zdarzenie `courier.location_updated` co ~20–30 s (Wolt/Uber; Glovo tego nie ma w push — nasza szansa).
12. **POD rozszerzalny** — blok `dropoff_verification{picture, pincode, signature, min_age}` (wzorzec Uber; wynik `image_url`/`gps`); na start minimalnie `finished_at+lat/lng` (jak Deligoo — nasz geofence 5b już to mierzy).
13. **Zwrot jako powiązana dostawa** (`ret_` + `related_deliveries`, Uber) — spójne z naszą DYSPOZYCJĄ w torze paczkowym.
14. **`deliverable_action`** (meet_at_door/leave_at_door) + `handshake/PIN` (Wolt/DoorDash).
15. **Auth OAuth2 client_credentials** (Uber/Stuart/Glovo) — docelowo; na start akceptowalny Bearer per partner (Wolt/Deligoo), bo prostszy dla małych POS-ów.
16. **Rozliczenia:** flat fee per delivery + `fee_components` (baza + `distance_based_fee`), faktura zbiorcza miesięczna, „płacisz tylko za zrealizowane" (komunikacja Stava/Deligoo).
17. **Katalog partnerów integracyjnych** (model Wolta 50+) — strona „wybierz swój POS", nie „zbuduj integrację".

### Tabela mapowania statusów (kanon ↔ liderzy)

| KANON (nasz) | Wolt Drive (event) | Uber Direct (new gen) | DoorDash (`delivery_status`) | Stuart | Deligoo | Deliverect Dispatch |
|---|---|---|---|---|---|---|
| CREATED | order.received | (created) | created | new/pending | waiting/pending | (po Create) |
| COURIER_ASSIGNED | (assigned) | (assigned) | confirmed | courier_assigned | pickups.set_as_assigned | COURIER_ASSIGNED |
| EN_ROUTE_TO_PICKUP | order.pickup_eta_updated | EN_ROUTE_TO_PICKUP | enroute_to_pickup | en_route_to_pickup | started | — |
| AT_PICKUP | order.pickup_arrival/started | ARRIVED_AT_PICKUP | arrived_at_pickup | almost_picking_up | pickup in_progress | — |
| PICKED_UP | order.picked_up | (pickup complete) | picked_up | picked_up | pickup finished | PICKED_UP |
| EN_ROUTE_TO_DROPOFF | order.dropoff_eta_updated | EN_ROUTE_TO_DROPOFF | enroute_to_dropoff | en_route_to_delivery | delivery started | — |
| AT_DROPOFF | order.dropoff_arrival/started | ARRIVED_AT_DROPOFF | arrived_at_dropoff | almost_delivering | delivery in_progress | — |
| DELIVERED | order.delivered/dropoff_completed | COMPLETED | delivered | delivered | completed | DELIVERED |
| CANCELLED | order.rejected/cancelled | (canceled) | cancelled | canceled | canceled | CANCELLED |
| FAILED/RETURNED | order.customer_no_show | FAILED (+is_returning) | returned | expired/failed | failed | — |

### Otwarte standardy — werdykt
- **Open Delivery (Abrasel, BR)** [ZW opendelivery.com.br]: jedyny żywy otwarty standard (OpenAPI YAML, moduły MERCHANT/ORDERS/**LOGISTICS**, sandbox developer.opendelivery.com.br). Użyć modułu LOGISTICS jako **referencji nazewnictwa** naszego kontraktu — nie wymyślać struktur od zera. Adopcja tylko brazylijska — nie jest wymogiem rynkowym w EU.
- schema.org / GS1 / Open Logistics Foundation / OSDM — inne domeny lub tylko słownik; **odrzucone** (uzasadnienia w raporcie źródłowym).
- **De-facto standard = wzorzec DaaS** (quote→create→webhooki→tracking→POD) + **Deliverect Dispatch** jako de-facto kontrakt „dostawca dla huba": my wystawiamy webhooki `Validate` (odpowiedź: `canDeliver`, `jobId`, `pickupTimeETA`, `distance`, `deliveryLocations[]`, `price{price,taxRate}`, `currency`), `Create`, `Cancel(reason)`; my wołamy `Update Delivery` (statusy `COURIER_ASSIGNED→PICKED_UP→DELIVERED`+`CANCELLED`, czasy, kurier, ETA) i `Cancel`. **Implementacja tych 5 operacji = warunek wejścia do Deliverect, a jednocześnie zdrowy szkielet naszego publicznego API.**

### Wniosek architektoniczny dla FAZY 3
Nasz wewnętrzny silnik (feasibility → plan → commit) już realizuje filozofię promesa→zlecenie; luka to warstwa publiczna: quotes+deliveries API, worker webhooków z podpisem i backoffem, kanon 10 statusów zmapowany na stany silnika (planned/assigned/picked_up/delivered + commitment_level), tracking_url (mamy), sandbox-symulator (mamy replay/shadow — wystawić), POD (geofence 5b + foto w apce = Faza 2 apki).
