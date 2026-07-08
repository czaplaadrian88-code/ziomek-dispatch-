# Plan pilotażu integracji — Timelly (wewnętrzny)

> Dokument planistyczny. Data: 2026-07-08. Bazuje na `00-STAN-OBECNY.md`, `03-ANALIZA-LUK.md` (pakiet IR v1),
> wzorcu `scripts/papu_dispatch_bridge/`. **To jest PLAN — wykonanie idzie protokołem zmian Ziomka
> (`memory/ziomek-change-protocol.md`, ETAP 0→7); każdy flip/restart/deploy = jawne ACK Adriana.**

## 0. Cel

Timelly (platforma zamówień, płatność online u nich) → zlecenie dostawy wpada do nas automatem, statusy + tracking
wracają. Pilotaż: 1–2 restauracje, pełna pętla na żywo, pomiar KPI od 1. dnia. Poziom odniesienia: model Wolt Drive.

## 1. Dwie ścieżki (rekomendacja: równolegle)

### Ścieżka A — most dedykowany (SZYBKA, na pilotaż) — REKOMENDOWANA na start
Kopia wzorca `papu_dispatch_bridge` → `timelly_dispatch_bridge`. Osobny proces (systemd oneshot + timer), jak
istniejące mosty. Pełna pętla już w tym wzorcu: **pull zamówień (HMAC) → wstrzyknięcie do gastro → odczyt przydziału →
push statusu/kuriera/ETA (HMAC)**.

- **Kierunek w przód:** poll `GET <Timelly>/…/pending-dispatch` (HMAC) → `add-zamowienie-zadmina` w gastro z markerem
  `#TL:<id>` (idempotencja jak `#PAPU:<uuid>`).
- **Przepływ powrotny:** czytnik `dispatch_v2.panel_client` → dla zleceń z `id_kurier` push realnego czasu odbioru +
  ETA + snapshot kuriera do `POST <Timelly>/…/status` (HMAC).
- **Co musimy zbudować:** clone + adaptacja klienta API Timelly, `restaurant_map.json` (uuid/id restauracji → id gastro),
  `city_map.json`, sekrety, jednostki systemd, `--dry-run` E2E. **Kod w ~80% istnieje.**
- **Czego potrzebujemy od Timelly:** (1) endpoint `pending-dispatch` (lista `placed`, podpis HMAC), (2) endpoint
  odbioru statusu (HMAC), (3) wspólny sekret HMAC, (4) numeryczne id restauracji pilotażowej do mapy.
- **Nakład:** ~**3–5 osobodni** (S), przy gotowych endpointach po stronie Timelly.
- **Ryzyko:** gastro w środku ścieżki (scraping + subprocess) — pojedynczy punkt awarii; **świadomie akceptowane w v1**
  (tak działa dziś most Papu na produkcji). Alerty OnFailure→Telegram jak w pozostałych mostach.

### Ścieżka B — publiczne API `/v1` (DOCELOWA, skalowalna)
Kontrakt z `10-KONTRAKT-PARTNERSKI-API-v1.md`: Timelly woła nasze `/v1/deliveries`, my webhookujemy statusy.
To jest właściwy fundament pod kolejnych partnerów (Restimo, Restaumatic, POS). Zakres = wycinek pakietu IR v1:

| Krok IR | Zakres dla Timelly | Nakład | Uwaga |
|---|---|---|---|
| **IR-1** | prefiks `/v1`, model błędów `{error_code,reason,details}`, jedna idempotencja `external_delivery_id`, rate-limit na kluczu | M (~1–2 tyg) | higiena — dotyka warstwy API panelu |
| **IR-2** | `POST /v1/deliveries` (tor jedzenia) spięte z **istniejącą** ścieżką OPS-02 `create_delivery` + `_push_to_ziomek` (NIE drugi silnik); `GET`, `cancel`, `tracking_url` w odpowiedzi, COD | L | reużycie, nie budowa od zera |
| **IR-3** | **most silnik→StatusEvent** w czasie rzeczywistym + maszyna przejść toru jedzenia + jawna anulacja (gastro 8/9) | L | **SERCE — ścieżka krytyczna; bez tego webhooki kłamią** |
| **IR-4** | worker webhooków (HTTP push, podpis `t=,v1=`, backoff, wersjonowanie) | M | wzór retry-cronów Papu |

- **Nakład (wycinek dla Timelly):** ~**6–9 osobotygodni**; pełny IR v1 (z sandbox/docs/self-service/DPA) = ~10–14 ot.
- **Kolejność wymuszona:** IR-3 **przed** publicznym włączeniem IR-4 (inaczej partner dostanie `delivered`, a potem
  `picked_up` po resurrect — 3 światy stanu). Budowa IR-3/IR-4 może iść równolegle, ale flip webhooków dopiero po IR-3.

**Rekomendacja:** ruszyć **A** teraz (pilotaż na żywo w tygodnie, nie kwartał), a **B** budować równolegle jako
docelowe; kontrakt `/v1` z dok. 10 jest tak dobrany, by przejście A→B **nie wymagało zmian po stronie Timelly**.

## 2. Protokół zmian Ziomka — mapowanie ETAP 0→7

Ścieżka A (most) to osobny proces (mniejsze ryzyko), ale wstrzykuje do gastro na produkcji → traktujemy poważnie.
Ścieżka B dotyka warstwy API panelu + silnika (IR-3) → **pełny protokół obowiązkowo**.

- **ETAP 0** — stan na żywo + testy bazowe ZIELONE (panel `pytest`, silnik `pytest tests/`), snapshot flag (`flags.json`,
  `flags.systemd.env`, drop-iny — 3 światy, ADR-004).
- **ETAP 1** — fix/wpięcie U ŹRÓDŁA: IR-2 przez istniejący `create_delivery`+`_push_to_ziomek` (nie nowy tor);
  IR-3 most silnik→`StatusEvent` u źródła zdarzeń (`event_bus`), nie łatka na renderze.
- **ETAP 2** — SOFT nie osłabia HARD: reguły silnika (R-DECLARED-TIME, R-35MIN, feasibility) nietknięte; `pickup_at`
  z API mapuje się na istniejący mechanizm `czas_kuriera` (frozen ETA), bez nowej ścieżki czasu.
- **ETAP 3** — MAPA KOMPLETNOŚCI: bliźniacze ścieżki RAZEM — worker webhooków obejmuje **każde** przejście kanonu
  (ASSIGNED/PICKED_UP/DELIVERED/RETURNED/CANCELLED); serializer statusu w jednym kanonie; idempotencja `external_delivery_id`
  mapowana na istniejące mechanizmy (`idempotency_key`, `Idempotency-Key`, `event_id`) — nie 5. wariant.
- **ETAP 4** — dowody nie deklaracje: flaga AUT06/AUT08 ON≠OFF (test), zdarzenia w logu, parytet mostu (A) vs API (B) na
  tym samym zleceniu, PEŁNA regresja panelu + silnika, E2E przez wszystkie dotknięte warstwy.
- **ETAP 5** — „warto + bez regresji": pilotaż A na 1–2 restauracjach = dowód pętli; okno obserwacji 2 dni przed
  rozszerzeniem/flipem B.
- **ETAP 6** — backup → py_compile → test → git log → **ACK Adriana** → 1 restart/flip; **NIGDY** telegram/peak bez OK.
  Flip flag `AUT06_POS_INTEGRATION`/`AUT08_OUTBOUND_API` = zmiana live, poza peakiem, z ACK.
- **ETAP 7** — rollback gotowy: most A = `systemctl disable --now dispatch-timelly-bridge.timer`; API B = flip flag OFF
  (hot-reload / restart panelu) + `git revert`.

## 3. Warunki wstępne bezpieczeństwa (przed jakimkolwiek wystawieniem publicznym — dot. B)

- **IR-0 / L14:** host-firewall + bind lokalny dla courier-api/OSRM/gps (dziś 0.0.0.0 tylko za Hetzner Cloud FW).
  Publiczna powierzchnia wyłącznie przez nginx 443. Ścieżka A (most, bez wystawiania portów) tego nie wymaga na pilotaż.
- **IR-6 / L24 (DPA):** wzorzec umowy powierzenia danych (my = podmiot przetwarzający dane klienta końcowego dla
  restauracji/Timelly-administratora) — **przed 1. produkcyjnym zleceniem**. Właściciel: prawnik + Adrian.

## 4. Kamienie milowe (proponowane, bez twardych dat — daty = decyzja Adriana)

1. Call techniczny z Timelly → domknięcie kontraktu pól/webhooków (dok. 10) + wybór kierunku pull vs push na pilotaż.
2. Timelly wystawia 2 endpointy HMAC (A) **lub** implementuje klienta `/v1` (B) — równolegle po naszej stronie: clone mostu (A).
3. `restaurant_map.json` pilotażowej restauracji + sekrety + `--dry-run` E2E.
4. Pilotaż A na żywo (1–2 restauracje), 2 dni obserwacji, KPI (dostawy, czas odbioru, % dostarczonych, oceny).
5. Równolegle: IR-1 → IR-3 (serce) → IR-4; flip B po IR-3 stabilnym, z ACK.
6. Migracja Timelly A→B bez zmian po ich stronie (kontrakt `/v1` stały).

## 5. Zależności / czego nie robimy w pilocie

- **Nie** wystawiamy webhooków przed domknięciem kanonu zdarzeń (IR-3) — kłamiące statusy = spalony partner.
- **Nie** przebudowujemy ścieżki krytycznej (obejście gastro = L21 etap 2, XL, osobny program) — gastro w środku = ryzyko przyjęte w v1.
- **Nie** budujemy pełnego self-service/sandbox/docs na sam pilotaż Timelly (to IR-5, po pilocie) — połączenie testowe stawiamy ręcznie.

---
*Powiązane: `03-ANALIZA-LUK.md` (pakiet IR v1), `08-RAPORT-KONCOWY.md` (kroki 14/30 dni), `10-KONTRAKT-PARTNERSKI-API-v1.md`,
`memory/ziomek-change-protocol.md`, `scripts/papu_dispatch_bridge/`.*
