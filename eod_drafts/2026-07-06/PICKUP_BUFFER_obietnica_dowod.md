# PICKUP-BUFFER — load-aware bufor OBIETNICY odbioru (zbudowane 2026-07-06, flaga OFF)

**Decyzja Adriana 06.07:** powierzchnia = OBIETNICA DECYZYJNA (opcje re-anchor i R6 odrzucone).
**Commity:** silnik `cd0bb25` (common.py + shadow_dispatcher.py + testy) · panel `fa27237` (coordinator-console, uśpiony fallback).

## Co robi
Przy propozycji silnik dolicza do OBIECYWANEGO czasu odbioru bufor per kubełek obciążenia × solo/worek
(semantyka 1:1 z `tools/pickup_slip_monitor`: luzno pf≥5 / srednio 2-4 / ciasno ≤1; solo = bag_after 1):

| cela | bufor (mediana 6d, review 04.07 n=1324) |
|---|---|
| ciasno solo | +25 | 
| ciasno worek | +13 |
| srednio solo | +24 |
| srednio worek | +12 |
| luzno solo | +17 |
| luzno worek | +7 |
Cap `PICKUP_BUFFER_MAX_MIN=30`. Brak danych (pf/bag None) → 0 = stara obietnica (fail-open).

**ADDYTYWNE pola** na serialized best: `pickup_buffer_min`, `eta_pickup_promised_utc`, `eta_pickup_promised_hhmm`
— obok starego `eta_pickup_hhmm`. **Wewnętrzne `eta_pickup_utc` NIETKNIĘTE** (scoring / feasibility HARD>60 /
R-LATE / extension_penalty bez zmian — wzorzec #8).

## Mapa kompletności
1. serializer best (`shadow_dispatcher._promised_pickup_fields`) — FIX ✓
2. panel `shadow_monitor.best_eta_pickup_hhmm` → 1-klik `time_arg` — FIX ✓ (uśpiony: preferuje promised gdy pole jest)
3. telegram_approver render — **N-D**: Telegram wyciszony 26.06 (nie wskrzeszać); przy ewentualnym re-enable C2 = pełny deploy, pola już są w decision dict
4. LOCATION A (kandydaci/alternatives) — **N-D**: obietnica dotyczy propozycji BEST (to ona jedzie w time_arg); ranking kandydatów = display
5. scoring/feasibility/R-LATE — **N-D celowo** (addytywność, wzorzec #8)
6. `pickup_slip_monitor` — bez zmian (mierzy wewnętrzną predykcję, nie obietnicę → baseline niezależny)

## Dowód (ETAP 5, replay okno 6d, n=1327)
- mediana błędu obietnicy per cela: **+7…+21 min → −3,9…+1,6** (obietnica przestaje systematycznie kłamać)
- `median|err|` **13,7 → 10,2 min (−26%)**, `mean|err|` 18,1 → 13,1 (−28%)
- NETTO per zlecenie: lepsza dla **914**, gorsza dla 413 (69/31) — cena centrowania mediany
- probe C14: mutacja bramki flagi zabita 2 testami; testy 6 + serializer-completeness + flag-registry zielone

## FLIP (za ACK Adriana; FLIPMASTER)
1. `flags.json`: `"ENABLE_LOAD_AWARE_PICKUP_BUFFER": true` (hot, silnik ≤tick)
2. `systemctl restart nadajesz-panel.service` (konsument fallbacku; poza peakiem)
3. Weryfikacja: `grep -c eta_pickup_promised_hhmm ../logs/shadow_decisions.jsonl` (świeże okno >0) + AI-HUB pokazuje późniejszy odbiór
4. Pomiar ON: offline join promised↔`picked_up_at` vs stary `eta_pickup` (pola w jsonl wystarczą)
⚠ Obserwacja 1. dnia: committed `czas_kuriera` będzie późniejszy → sprzężenie z R27 ±5 (soft) — sprawdzić,
czy TSP nie ODRACZA fizycznie możliwych odbiorów (jedzenie gotowe wcześniej); regres → rollback.
**Rollback:** flaga false (hot) — panel fallback wraca sam (brak pola).
