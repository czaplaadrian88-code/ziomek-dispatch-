# FAIL-03 K2 — spec: KOORD-cisza → PROPOSE (z odroczeniem) / jawny KOORD-alert

**Status:** DRAFT — wymaga ACK Adriana + restart `dispatch-telegram`. NIE deployować bez ACK.
**Uzasadnienie danymi:** `FAIL03_groundtruth_diagnoza.md` (76% zleceń-w-ciszę realnie dowiezione ≤35min przez odroczenie odbioru; Ziomek przeszacowuje breach medianowo +7min zakładając odbiór natychmiast).

## Problem (przypomnienie)
`telegram_approver.py:1964` tailer wysyła na Telegram TYLKO `verdict=PROPOSE`. Near-term `verdict=KOORD` (best_effort_r6_breach / low_score / no_solo, nie-early-bird, nie-firmowe) = cisza do operatora. ~8-18%/dzień (K1). Łamie „ZAWSZE PROPONUJ".

## Decyzje Adriana 2026-06-05 (wiążące)
1. **ZAWSZE propozycja z banerem** — jeden tor dla WSZYSTKICH powodów (r6_breach, low_score, no_solo). Nigdy cisza, nigdy „zdecyduj ręcznie"-KOORD. Baner niesie trudność; operator/autonomia widzą, że to ciężki przypadek, ale propozycja JEST.
2. **Brak max-cap odroczenia** — zamiast twardego limitu: **soft kara rosnąca** z minutami odroczenia (Ziomek odracza ile trzeba, ale woli mniej). Gradient, nie threshold (Lekcja QA-10).
3. **K2 obejmuje WSZYSTKICH kurierów** — łącznie z no-GPS / NOT_SEEN — bo Ziomek ma być autonomiczny. Nie wolno milczeć tylko dlatego, że jedyny dostępny kurier nie ma GPS.

## Zachowanie docelowe — JEDEN tor: zawsze PROPOSE best_effort + baner

Każdy near-term KOORD-cisza (`_AP_KOORD_SILENCE_PREFIXES`, nie-early-bird, nie-firmowe) → `verdict=PROPOSE` najlepszego dostępnego kandydata + baner opisujący trudność. Baner zależny od sytuacji:
- **odroczenie trzyma R6** (76% wg danych): `⚠ odbiór ~HH:MM (odroczony +Xmin), dowóz ~Ymin — R6 OK z odroczeniem` (`reason=fail03_k2_defer`).
- **R6 łamane mimo odroczenia** (~24% genuinely-hard): `⚠ łamie R6 o Xmin nawet z odroczeniem — najlepsza z dostępnych` (`reason=fail03_k2_best_effort`).
- **kandydat poniżej progu** (ex-low_score/no_solo): `⚠ słaba opcja (score X) — brak lepszej, najlepsza dostępna` (`reason=fail03_k2_lowscore`).
- **kandydat bez GPS** (ex-NOT_SEEN, p. niżej): dorzuć `· kurier bez GPS, pozycja szacowana`.
- **Zgodne z** [[feedback_always_propose_defer_pickup]] + [[feedback_two_hard_rules_defer_over_extend]] (odrocz odbiór, nie łam dowozu).

## Odroczenie odbioru = soft kara rosnąca (decyzja #2)
Nie liczymy „target aż się zmieści w cap". Zamiast tego objective/scorer dostaje **rosnącą karę za minuty odroczenia** `target_pickup − pickup_ready`:
- `FAIL03_DEFER_SOFT_PENALTY` progresywna (np. 0 do +5min za darmo [w granicy R-5MIN late-pickup], potem rośnie liniowo/kwadratowo). Bez twardego odcięcia — przy braku alternatyw Ziomek odroczy mocno, ale taki kandydat naturalnie spadnie w rankingu jeśli istnieje mniej-odraczający.
- Wpina się w istniejący stack soft-kar (analogicznie `bonus_v3273_wait_courier` / `late_pickup_soft`). Sanity: median realnego defer człowieka = 22min → kara nie może być tak stroma, by 22min = de-facto odrzucenie.

## Zakres floty: WSZYSCY, też no-GPS (decyzja #3) — NAJWIĘKSZY blok pracy
Dziś 22% zleceń-w-ciszy poszło do kuriera, którego Ziomek **w ogóle nie miał we flocie** (no-GPS → brak pozycji → poza snapshotem). Dla autonomii K2 musi ich rozważać.
- **Hierarchia pozycji (fallback)** dla kuriera bez świeżego GPS: świeży GPS → ostatnia pozycja z `courier_status_events` (lat/lon przy pickup/delivery w apce — breadcrumb mimo GPS-off) → ostatni znany GPS → ostatni `delivered`/`assigned` stop → grafik/`BIALYSTOK_CENTER`. Każdy fallback oznaczony `pos_source` + baner „pozycja szacowana".
- **Relaksacja demote** (V3.16 `_demote_blind_empty`): w ścieżce K2 (gdy alternatywa = cisza) NIE wykluczać no-GPS — proponować z banerem. Poza K2 demote zostaje.
- **⚠ ZALEŻNOŚĆ KRYTYCZNA (uczciwie):** apka jest KANAŁEM dostarczenia propozycji. Kurier z **martwą** apką nie zobaczy zlecenia → propozycja w próżnię. NOT_SEEN z korpusu byli AKTYWNI (509: 77 przypisań/33 odbiory 06-04) → app żyła, tylko GPS off → OK do proponowania. Ale pełna autonomia wymaga sygnału „apka żyje" (shift-confirm / `courier_status_events` świeże / panel-status) by odróżnić „GPS off ale pracuje" od „apka padła". To **precondition autonomii**, łączy się z FAIL-07 + SHIFT_NOTIFY. Bez tego sygnału: proponować no-GPS, ale z banerem „niepotwierdzony" i preferować potwierdzonych.

## Wykluczenia (zostają)
`early_bird` (mtp ≥ EARLY_BIRD_THRESHOLD_MIN), firmowe (`FIRMOWE_KONTO_ADDRESS_IDS`). Czasówki mają własny alert (`czasowka_scheduler`) — nie dotykać.

## Punkty w kodzie
1. **Fleet — włącz no-GPS (decyzja #3)** — `courier_resolver` / `dispatchable_fleet`: w ścieżce K2 nie wykluczaj no-GPS; nadaj pozycję wg hierarchii fallback (świeży GPS → `courier_status_events` breadcrumb → ostatni GPS → ostatni stop → city center), `pos_source` znaczony. To NAJWIĘKSZY blok (dotyka floty, nie tylko verdiktu).
2. **Defer soft-penalty (decyzja #2)** — `FAIL03_DEFER_SOFT_PENALTY` progresywna na `target_pickup − pickup_ready`, BEZ cap; wpięta w stack soft-kar (jak `bonus_v3273_wait_courier`). Kandydat odracza ile trzeba, ale mniej-odraczający wygrywa naturalnie.
3. **Verdict path — JEDEN tor (decyzja #1)** (`dispatch_pipeline.py`/`shadow_dispatcher.py`): gdzie powstaje KOORD z `_AP_KOORD_SILENCE_PREFIXES` → pod flagą K2 ZAWSZE `verdict=PROPOSE` + `reason` wg sytuacji + flagi banera (`defer_min`, `r6_breach_min`, `low_score`, `no_gps`). Serializacja LOC A+B (lekcja #80).
4. **Tailer** (`telegram_approver.py:1964`) — buduj baner z flag (defer/breach/lowscore/no-GPS). NIE ma już toru B-KOORD-alert; wszystko = PROPOSE.
5. **Demote relax** (V3.16 `_demote_blind_empty`) — w ścieżce K2 nie demotuj no-GPS do dna (inaczej #1+#3 się gryzą).
6. **Flaga** `ENABLE_FAIL03_K2_PROPOSE` (default OFF). Bez cap-flagi.

## Rollout (per Z2 jakość ponad szybkość)
1. **Shadow** (flaga OFF): K2 liczy co BY wysłał (tor A/B + defer target) obok live, serializuje `fail03_k2_shadow`. Zero zmiany. 3-5 dni PO recalib (06-05) — recalib zmienia wolumen r6_breach.
2. **Weryfikacja**: `fail03_outcome_join.py` — czy K2-defer-target zgadza się z realnym defer człowieka; czy tor B faktycznie rzadki.
3. **Canary**: flaga ON, ale najpierw tylko `mtp<15min` (najpilniejsze) → obserwacja override rate na Telegramie.
4. **Full live**: ACK + restart `dispatch-telegram`. Rollback: flaga OFF (hot-reload) — tailer wraca do PROPOSE-only.

## Kalibracja pesymizmu (równolegle, niezależne)
est_breach best_effort przeszacowany medianowo +7min (założenie immediate-pickup). Rozważyć liczenie breach pod realny defer ZANIM trafi do gałęzi KOORD — część fałszywych KOORD zniknie u źródła (mniej do przepisywania w K2).

## Decyzje Adriana 2026-06-05 (zamknięte) + wynikające zależności
1. ✅ Zawsze PROPOSE + baner (jeden tor, brak KOORD-alert).
2. ✅ Brak cap odroczenia → soft kara rosnąca.
3. ✅ K2 obejmuje wszystkich (no-GPS też) — autonomia.
- **Zależność z #3:** pełna autonomia no-GPS wymaga sygnału „apka żyje" (shift-confirm / `courier_status_events` świeże), inaczej ryzyko propozycji do martwej apki. Do shadow/canary: proponować z banerem „pozycja szacowana/niepotwierdzona", preferować potwierdzonych. Twardy sygnał app-alive = osobny precondition (FAIL-07 + SHIFT_NOTIFY).
- **Konsekwencja:** #3 czyni K2 znacząco większym (dotyka `courier_resolver`/floty + demote, nie tylko verdiktu). Kolejność: shadow #1+#2 (verdict+defer, łatwiejsze) najpierw; #3 (fleet no-GPS) jako druga faza shadow, bo dotyka core selekcji.

## NIE rób bez ACK
Restart `dispatch-telegram` = twarda reguła ACK. Cała zmiana hot-path verdiktu = per-step ACK + py_compile + testy + shadow-first.
