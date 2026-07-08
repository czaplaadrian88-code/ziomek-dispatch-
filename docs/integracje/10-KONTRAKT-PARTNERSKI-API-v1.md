# Nadajesz — Kontrakt Integracji Partnerskiej (Delivery API) v1

> Dokument dla zespołu technicznego partnera (np. Timelly). Opisuje **kontrakt docelowy `/v1`**,
> przeciw któremu programujecie integrację. API jest w finalizacji — do pilotażu udostępniamy
> dedykowane połączenie testowe (patrz §12). Kontrakt `/v1` jest stabilny: to, co tu opisane, nie zmieni
> się pod Wami, niezależnie od tego, co wymieniamy po naszej stronie.

## 1. Model integracji

Jesteśmy **flotą kurierską (Delivery-as-a-Service)** z własnym systemem dyspozytorskim i aplikacją GPS
kuriera. Przepływ:

1. Klient zamawia i płaci u Was (Timelly).
2. Wasz system **tworzy u nas zlecenie dostawy** (`POST /v1/deliveries`) — automatycznie, bez ręcznego przepisywania.
3. My przydzielamy kuriera, realizujemy odbiór i dostawę.
4. **Odsyłamy Wam statusy webhookami** (push) + zwracamy link do śledzenia dla klienta końcowego.
5. Fallback odczytu: `GET /v1/deliveries/{external_delivery_id}` (polling, gdy webhook nie dojdzie).

Baza: `https://api.nadajesz.pl/v1` (produkcja) · `https://api-sandbox.nadajesz.pl/v1` (test).
Format: JSON, UTF-8. Czasy: ISO-8601 z offsetem (np. `2026-07-08T13:20:00+02:00`). Kwoty: **grosze** (int).

## 2. Uwierzytelnianie

Klucz API per połączenie (wystawiamy Wam przy onboardingu; pokazywany raz, z możliwością rotacji).

```
Authorization: Bearer <API_KEY>
```

Klucz ma zasięg (scope) ograniczony do Waszego konta i przypisanych lokali. Osobne klucze dla testu i produkcji.

## 3. Wycena — `POST /v1/quotes` (opcjonalne)

Pozwala pokazać klientowi koszt i ETA przed potwierdzeniem. Wycena ma TTL.

```json
POST /v1/quotes
{
  "pickup":  { "location_ref": "REST-123", "address": "Lipowa 5, Białystok" },
  "dropoff": { "address": "Wiejska 12/3, Białystok", "lat": 53.1234, "lng": 23.1567 },
  "pickup_at": "2026-07-08T13:20:00+02:00"
}
```
```json
200 OK
{
  "quote_id": "qte_a1b2c3",
  "fee": 1490,
  "currency": "PLN",
  "pickup_eta": "2026-07-08T13:22:00+02:00",
  "dropoff_eta": "2026-07-08T13:47:00+02:00",
  "expires_at": "2026-07-08T13:30:00+02:00"
}
```

`quote_id` można podać przy tworzeniu dostawy, żeby zamrozić cenę. Poza obszarem: `422 DROPOFF_OUTSIDE_OF_DELIVERY_AREA`.

## 4. Utworzenie dostawy — `POST /v1/deliveries`

Nagłówek **`Idempotency-Key`** obowiązkowy (zalecane = `external_delivery_id`). Ponowienie z tym samym
kluczem i tymi samymi danymi → `200` z istniejącą dostawą; z innymi danymi → `409 DUPLICATE_ORDER`.

```json
POST /v1/deliveries
Idempotency-Key: TL-2026-000123
{
  "external_delivery_id": "TL-2026-000123",
  "quote_id": "qte_a1b2c3",
  "pickup": {
    "location_ref": "REST-123",
    "name": "Pizzeria Bella",
    "address": "Lipowa 5, Białystok",
    "phone": "+48111222333",
    "notes": "wejście od podwórza"
  },
  "dropoff": {
    "address": "Wiejska 12/3, Białystok",
    "lat": 53.1234, "lng": 23.1567,
    "contact": { "name": "Jan K.", "phone": "+48555666777" },
    "notes": "domofon 12, 2. piętro",
    "contactless": false
  },
  "pickup_at": "2026-07-08T13:20:00+02:00",
  "order": {
    "notes": "bez cebuli",
    "items_summary": "2x pizza, 1x cola"
  },
  "payment": {
    "type": "prepaid",
    "cod": { "amount": 0, "payment_forms": [] }
  }
}
```
```json
201 Created
{
  "id": "dlv_9f8e7d",
  "external_delivery_id": "TL-2026-000123",
  "status": "CREATED",
  "tracking_url": "https://gps.nadajesz.pl/t/Ab3kD9",
  "fee": 1490,
  "currency": "PLN",
  "pickup_eta": "2026-07-08T13:22:00+02:00",
  "dropoff_eta": "2026-07-08T13:47:00+02:00",
  "cancellable": true
}
```

- **`payment.type`**: `prepaid` (klient zapłacił u Was — domyślne dla Timelly, `cod.amount=0`) lub `cod`
  (pobranie u klienta; `payment_forms`: `cash`, `card_on_delivery`). COD obsługujemy — to nasza przewaga nad częścią rynku.
- **`pickup_at`** = żądana godzina odbioru; trzymamy ją twardo (kurier pod restauracją na czas).
- Adres z `lat`/`lng` przyspiesza i uściśla geokodowanie (zalecane, jeśli macie pinezkę z checkoutu).

## 5. Mapowanie pól (co przesyłacie → nasze pole)

| Dane od Was (z maila) | Pole w API |
|---|---|
| adres dostawy | `dropoff.address` (+ `lat`/`lng` jeśli macie) |
| telefon klienta | `dropoff.contact.phone` |
| dane restauracji | `pickup.location_ref` (mapowanie ustalane raz przy onboardingu) + `pickup.name/address/phone` |
| godzina odbioru | `pickup_at` |
| uwagi do zamówienia | `order.notes` (do kuchni) / `dropoff.notes` (dla kuriera) |
| informacja o płatności | `payment.type` + `payment.cod` |
| Wasze ID zamówienia | `external_delivery_id` (= klucz idempotencji i klucz odczytu) |

## 6. Odczyt — `GET /v1/deliveries/{external_delivery_id}`

Zwraca aktualny stan, kuriera i czasy (estimated/actual). Używać jako fallback pollingu (webhook = kanał główny).

```json
200 OK
{
  "external_delivery_id": "TL-2026-000123",
  "status": "PICKED_UP",
  "courier": { "name": "Michał K.", "phone": "+48500100200", "lat": 53.128, "lng": 23.160 },
  "pickup_eta": "2026-07-08T13:22:00+02:00",
  "picked_up_at": "2026-07-08T13:24:11+02:00",
  "dropoff_eta": "2026-07-08T13:47:00+02:00",
  "tracking_url": "https://gps.nadajesz.pl/t/Ab3kD9",
  "cancellable": false
}
```

## 7. Anulowanie — `POST /v1/deliveries/{external_delivery_id}/cancel`

```json
{ "reason": "customer_cancelled" }
```
Polityka okna anulowania (darmowe do momentu odbioru; po odbiorze — do ustalenia handlowego) jest zaszyta;
pole `cancellable` w obiekcie dostawy mówi, czy anulacja jest jeszcze możliwa. Po oknie: `409 CANCELLATION_WINDOW_PASSED`.

## 8. Webhooki (statusy zwrotne)

Rejestrujecie URL odbiorczy + otrzymujecie `webhook_secret`. Wysyłamy `POST` z podpisem, oczekujemy `2xx` jako ACK;
brak ACK → ponawiamy z narastającym odstępem (exponential backoff). Zdarzenia mają stabilne `event_id` (idempotencja u Was).

**Podpis** (nagłówek `Nadajesz-Signature`, wzorzec Stripe): `t=<unix_ts>,v1=<HMAC_SHA256(secret, "t.body")>`.
Weryfikacja: policzcie HMAC-SHA256 z `"<t>.<surowe_body>"` i porównajcie stałoczasowo z `v1`; odrzućcie `t` starsze niż ~5 min.

```json
POST <wasz_webhook_url>
Nadajesz-Signature: t=1751979611,v1=5f3a...c9
{
  "event_id": "evt_77aa12",
  "type": "delivery.status_changed",
  "occurred_at": "2026-07-08T13:24:11+02:00",
  "data": {
    "external_delivery_id": "TL-2026-000123",
    "status": "PICKED_UP",
    "courier": { "name": "Michał K.", "phone": "+48500100200" },
    "dropoff_eta": "2026-07-08T13:47:00+02:00",
    "tracking_url": "https://gps.nadajesz.pl/t/Ab3kD9"
  }
}
```

Zdarzenie `delivered` niesie dodatkowo potwierdzenie doręczenia (na start: `delivered_at` + `gps{lat,lng}`;
foto/podpis — etap 2). Dla COD niesie wynik pobrania.

## 9. Katalog statusów (kanon ↔ Wasze pojęcia z maila)

| Status (kanon) | Znaczenie | Wasz odpowiednik |
|---|---|---|
| `CREATED` | zlecenie przyjęte | **przyjęte** |
| `COURIER_ASSIGNED` | przydzielono kuriera | przyjęte / przydzielony |
| `EN_ROUTE_TO_PICKUP` | kurier jedzie po odbiór | — |
| `AT_PICKUP` | kurier pod restauracją | — |
| `PICKED_UP` | odebrane z restauracji | **kurier odebrał** |
| `EN_ROUTE_TO_DROPOFF` | w drodze do klienta | **w drodze** |
| `AT_DROPOFF` | kurier pod adresem klienta | — |
| `DELIVERED` | dostarczone | **dostarczone** |
| `CANCELLED` | anulowane | **anulowane** |
| `FAILED` / `RETURNED` | nieodebrane / zwrot | anulowane (podtyp) |

Wysyłamy pełny zbiór; jeśli chcecie mapować tylko 5 głównych stanów — pozostałe traktujcie jako informacyjne.

## 10. Model błędów

```json
{ "error_code": "DROPOFF_OUTSIDE_OF_DELIVERY_AREA", "reason": "Adres poza obszarem dostawy", "details": {} }
```
Kody: `DROPOFF_OUTSIDE_OF_DELIVERY_AREA`, `DUPLICATE_ORDER`, `QUOTE_EXPIRED`, `CANCELLATION_WINDOW_PASSED`,
`VALIDATION_ERROR` (422, z listą pól). `429` przy przekroczeniu limitu na kluczu API.

## 11. Strefy dostawy — `GET /v1/delivery-areas`

Zwraca obsługiwane obszary (poligony/promienie), żebyście mogli sprawdzić zasięg przed ofertą w checkoucie.

## 12. Środowisko testowe / pilotaż

- Na start udostępniamy **dedykowane połączenie testowe** (klucz sandbox + tryb symulacji statusów), abyście przeszli
  pełen scenariusz end-to-end bez realnych kurierów.
- Jeśli w pilocie wygodniej Wam, żebyśmy to **my odpytywali Wasz system** (zamiast Wy wołacie nasze `/v1`), mamy sprawdzony
  wzorzec takiego mostu (HMAC, pull zamówień + push statusów) — ustalamy na callu technicznym. **Docelowo rekomendujemy
  kierunek z §1 (Wy → nasze `/v1`, my → webhooki do Was)** — jest stabilny i skalowalny.

---
*Kontakt techniczny: (uzupełnić). Wersja kontraktu: v1 (2026-07-08).*
