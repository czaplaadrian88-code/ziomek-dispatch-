# GPS→ETA stitch — poprawiony design v2

**Status:** design gotowy; implementacja i wydanie **HOLD** do pełnego pomiaru na spójnym snapshotcie. Ten commit nie zmienia silnika, flag, konfiguracji runtime ani danych. Wycofuje twierdzenie o „suficie 1,3%”.

## 1. Werdykt po niezależnej weryfikacji

Sol trafnie odrzucił v1. Kod potwierdza cztery luki:

1. Kanoniczny oracle odrzuca GPS, gdy `courier_id` źródła nie zgadza się z kurierem z wiersza zamówienia (`tools/eta_ground_truth.py:340-345,361-418`). Join wyłącznie po `order_id` zawyżał pokrycie.
2. Istnieje pickup proxy: `restaurant_last_inside_at`, zapisane historycznie jako `departed_restaurant`. Oracle używa go wraz z GPS arrival do pełnej pary delivery (`:361-387,789-897`). Pickup nie może bezwarunkowo zostać na kliku.
3. Zmiana targetu przebudowuje empirical, LGBM, historię pace i conformal; udział rekordów nie jest granicą zmiany MAE.
4. Target ma więcej writerów i konsumentów niż siedem wskazanych w v1. Pełna mapa jest w `completeness_map.tsv`.

## 2. Pomiar read-only i granice dowodu

`measure_coverage_v2.py` realizuje join fail-closed po `(order_id, courier_id)`, kanoniczną precedencję app→server, filtr confidence/source/as-of oraz pełną parę `last_inside→arrival`. Spójny snapshot SQLite, wykonany przez backup API, otwiera przez `mode=ro&immutable=1`; skrypt emituje wyłącznie agregaty.

| Dowód | Wynik | Interpretacja |
|---|---:|---|
| snapshot GPS | 2399 rekordów; 2047 high | populacja źródła, nie mianownik trenera |
| high GPS ∩ assignment outcomes | 1312 | tylko kontrola joinu; outcomes ≠ trainer |
| zgodny kurier / mismatch | 1241 / 71 | mismatch 5,412% na tym wsparciu dowodzi, że filtr jest materialny |
| click minus GPS arrival, high GPS | mediana +2,35 min | opis źródła; nie efekt modelu |
| dawne `any=2109`, `high=1805`, trainer=9464 | tylko upper bounds | join order-only i dwie różne populacje |

W sandboxie brak snapshotu `eta_calib.db`, `restaurant_dwell.json` i `courier_ground_truth.json`. Dlatego skorygowane pokrycie trenera i pickup proxy pozostają **UNMEASURED**, a nie estymowane przez odjęcie 5,412% od 1805. Kanoniczne pełne polecenie i hashe dostępnego wejścia zapisano w `coverage_v2.json`. Pomiar ma zostać powtórzony na jednym zamrożonym snapshotcie wszystkich czterech źródeł.

Nie istnieje uczciwy nowy „sufit”. Nawet dokładne pokrycie mówi tylko, ilu wierszom można zmienić label; nie ogranicza wpływu refitu drzew, kwantyli, historii kuriera, offsetów conformal ani predykcji pozostałych wierszy.

## 3. Kontrakt targetu

Konfiguracja dostaje enum `target_policy`, domyślnie `click_v1`:

- `click_v1`: obecne targety i pace, bajtowo bez zmian.
- `observable_proxy_v1` pickup: `restaurant_last_inside_at - czas_kuriera`, wyłącznie gdy restaurant proxy ma tego samego kuriera, `_source=gps_geofence`, co najmniej dwa punkty wewnątrz geofence i timestamp nie wykracza poza snapshot.
- `observable_proxy_v1` delivery: `delivery_arrival_at - restaurant_last_inside_at`, tylko dla kompletnej, zgodnej-kuriersko pary i dozwolonego okna czasu. Arrival zachowuje precedencję app geofence przed high-confidence server geofence.
- Brak jednego końca pary oznacza fallback **całego** targetu danego etapu do `click_v1`. Nie wolno tworzyć hybrydy click→GPS ani GPS→click. Niepełne endpointy można zachować tylko jako outcome-only observability. Wiersze z fallbackiem mogą stabilizować trening wariantu hybrydowego, ale nie wchodzą do proxy-KPI ani nie mogą dać promocji. Obowiązuje bramka bindingu: kompletne proxy `n≥200` i coverage `≥60%`; niżej wynik to HOLD.

Nazwy i dokumentacja mówią „observable proxy”, nigdy „physical pickup/handoff”. Każdy wiersz niesie `target_policy`, eligibility, źródła, confidence i resolver schema/hash. Wszystkie timestampy, targety i provenance trafiają do `OUTCOME_ONLY_FIELDS`; nie mogą stać się cechą servingową.

## 4. Architektura i kompletność

Oracle i kalibrator mają korzystać z jednego publicznego, czystego resolvera zamiast dwóch implementacji joinu. Nowy `targets.py` staje się jedynym selektorem targetu **i pace**. Wywołują go builder historii, empirical (`models.py:129`), LGBM (`:243`), evaluate/conformal/baselines, promotion i replay.

Zmiana SQLite jest addytywna, idempotentna i transakcyjna. Migruje istniejącą tabelę 24-polową, a pozycyjny insert (`features.py:389`) zastępuje jawna lista kolumn z UPSERT-em. DDL, migracja, fixture i writer są jednym kontraktem.

`features.py:405` dziś tylko ładuje YAML; deklarowany env override nie istnieje. Design wymaga typed allowlist `ETA_CALIB_*` dla enum/path/bool, odrzucenia nieznanych kluczy i testu efektywnego env procesu. To konfiguracja narzędzia shadow, nie nowa flaga decyzji silnika.

Artefakty click i proxy mają osobne ścieżki oraz policy/resolver/support fingerprint. Serving i `eta_trust.py:224-298` odrzucają obcą lub brakującą policy. Obecny champion click pozostaje nietknięty. Repo-wide cross-check ujawnił też równoległy resolver w `decision_episode_v1.py:780-910`; musi przejść na wspólny kanon lub jawny adapter. `completeness_map.tsv` obejmuje 62 miejsca: binding i bramkę coverage, writery i pozostałych konsumentów źródeł, schema/migrację, pace, oba modele, conformal, promotion, artefakty, jsonl, health, serving, trust, dokumentację i testy. `TAK` oznacza zakres przyszłej implementacji; `N-D` ma lokalny powód i test granicy.

## 5. Uczciwy eksperyment i etapy

Porównanie `click_v1` z `observable_proxy_v1` używa tego samego immutable snapshotu i tych samych zewnętrznych foldów czasowych. Dla każdej policy od zera powstają target, pace, empirical, LGBM i conformal. Raportuje się osobno pickup i delivery, pełną populację każdej policy oraz dokładne wspólne wsparcie courier-matched. Metryki: bias, MAE, pinball i coverage przedziałów; niepewność przez paired block bootstrap po dniu i kurierze. Werdykt uwzględnia też liczbę fallbacków i drift po źródle.

1. **E0 — design/pomiar:** wykonane częściowo; pełny run jest bramką HOLD.
2. **E1 — wspólny resolver/config:** parity oracle, enum default OFF (`click_v1`), realny typed env override.
3. **E2 — schema/target:** migracja old-24, jawny insert, outcome-only provenance, centralny target+pace; brak serving change.
4. **E3 — wszystkie bliźniaki:** empirical, LGBM, history, evaluate, conformal, promotion, replay i izolowane artefakty; pełna regresja OFF=baseline.
5. **E4 — pełny refit/shadow:** paired replay, golden/mutation oracle, metryka do jsonl i minimum dwa dni obserwacji; bez promocji.
6. **E5 — dokumentacja/handoff:** kontrakt i wynik obserwacji. W tym zadaniu globalna pamięć jest N-D, bo zapis dozwolono tylko w klonie.
7. **E6 — osobny release:** dopiero pozytywny werdykt i jawny ACK ownera na config/serving/restart; health, PID, fingerprint i smoke po wydaniu.

## 6. Ryzyko i rollback

Największe ryzyka to małe pokrycie kompletnej pary, selection bias GPS, błędna tożsamość po reassignment, leakage outcome→feature i przypadkowe porównanie policy na różnym wsparciu. Bramki w mapie są fail-closed.

Rollback implementacji: `target_policy=click_v1`, osobne artefakty proxy odłączone od servingu, kompatybilna wstecz migracja (nowe nullable kolumny) i jawny revert kodu. Ten design nie wymaga decyzji biznesowej, migracji live, flag flipu, deployu ani restartu; wszystkie pozostają poza zakresem i wymagają nowego ACK.
