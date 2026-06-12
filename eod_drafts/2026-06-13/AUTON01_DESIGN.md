# AUTON-01 — ścieżka auto-assign za flagą (projekt, 2026-06-13 nocna sesja)

**Status:** SHADOW (telemetria compute-zawsze) + szkielet egzekutora OFF.
**Flip:** po E7 (at#131 17.06), progi na NOWYM marginie Z-10, za jawnym ACK Adriana, osobny krok E2E.
**Decyzja Adriana (handoff 12.06 ~09:00):** budować ZA FLAGĄ teraz, flip po E7, osobna sesja = ta.

---

## 1. Stan zastany

- `AUTO_APPROVE_THRESHOLD/MIN_GAP/ENABLED` (`common.py` ~586-588) = placeholdery F2.1c, **zero call-site** (zweryfikowane grep). `auto_route='AUTO'` to wyłącznie etykieta „🤖 PEWIEN" w Telegramie — żadna ścieżka nie wykonuje przypisania.
- Klasyfikator Fazy 7 (`auto_proximity_classifier.classify_auto_route`) działa na każdej decyzji PROPOSE: progi T1/T2/T3 (margin/score/tier/pool), bucket HIGH_RISK 14-17, edge-routing (czasówki→ACK, best_effort→ALERT), C7 `best_is_score_top`, margin wg **E2-Z10** (`ENABLE_F7_MARGIN_FINAL_RANKING`, env default ON): `score(best) − max(score POZOSTAŁYCH feasible)`.
- Realny assign istnieje TYLKO w `telegram_approver` (przycisk ASSIGN → `run_gastro_assign` → subprocess `scripts/gastro_assign.py --id --kurier --time` → POST `/admin2017/new/orders/przypisz-zamowienie`).

## 2. Architektura (3 warstwy)

```
dispatch_pipeline._classify_and_set_auto_route   ← (już istnieje, wszystkie silniki)
   └─ NOWE: auto_assign_gate.evaluate_auto_assign(result, order_event, INFORMED_POS_SOURCES)
      → result.would_auto_assign: bool, result.auto_block_reasons: [str]
      CZYSTA funkcja, COMPUTE-ZAWSZE (lekcja #186), zero I/O, zero wpływu na decyzję.

shadow_dispatcher._serialize_result              ← LOCATION B (top-level, obok auto_route)
   → pola "would_auto_assign" + "auto_block_reasons" w shadow_decisions.jsonl

shadow_dispatcher (po _append_decision)          ← TYLKO proces dispatch-shadow
   └─ NOWE: auto_assign_executor.maybe_execute(record, result, payload)
      pierwsza linia: if not C.decision_flag("ENABLE_AUTO_ASSIGN"): return None
      (kanon ETAP4, flags.json=false → hot killswitch; przy OFF NIE wykonuje NIC)
```

Dlaczego hook egzekutora w `shadow_dispatcher`, nie w `dispatch_pipeline`:
`assess_order` woła też dispatch-czasowka i dispatch-plan-recheck (re-decyzje) —
egzekucja stamtąd groziłaby auto-assignem z procesu re-planowania. Telemetria
liczona wszędzie (spójny silnik, fingerprint), egzekucja tylko z jednego miejsca.
Dlaczego NIE w `telegram_approver`: dyrektywa „dispatch-telegram NIE DOTYKAĆ";
notyfikacja post-hoc idzie przez `telegram_utils.send_admin_alert` (HTTP do API,
bez dotykania demona).

## 3. Bramka AUTO (warunki `would_auto_assign`)

Warstwa 1 — **klasyfikator Fazy 7 musi dać AUTO** (reuse zamiast równoległego
systemu progów): margin E2-Z10 ≥ próg poziomu (T1=15), score ≥ floor, tier w
whiteliście (T1: gold/std+), pool_feasible ≥ 2, C7 best==score-top, HIGH_RISK
14-17 zaostrza, edge-cases (czasówka/best_effort/solo_fallback/shift_end/
parser_degraded/KK-dinner) odpadają w klasyfikatorze.

Warstwa 2 — **dodatkowe twarde bramki AUTON-01** (każda = osobny block-reason;
zbierane WSZYSTKIE, nie first-fail — kalibracja E7 widzi pełny rozkład):

| # | Bramka | Powód (auto_block_reasons) | Uzasadnienie |
|---|---|---|---|
| G1 | verdict == PROPOSE | `verdict_not_propose:<v>` | KOORD nigdy auto (dyrektywa) |
| G2 | auto_route == AUTO | `classifier_not_auto:<route>:<reason>` | progi Fazy 7 na marginie Z-10 |
| G3 | brak `auto_route_context` → fail-closed | `no_auto_route_context` | bez kontekstu nie ma dowodu jakości |
| G4 | NIE czasówka (order_event, pas i szelki do edge w klasyfikatorze) | `czasowka` | wyłączenie z promptu; czasówki = osobny tor E5 |
| G5 | address_id ∉ PACZKA_ADDRESS_IDS ∪ FIRMOWE_KONTO_ADDRESS_IDS (161, 232-236) | `paczka_firmowe` | paczki/firmowe = inny kontrakt czasowy |
| G6 | tier ≠ "new" | `new_courier_ramp` | RAMPA nowych (T1/T2 i tak wyklucza; jawnie dla T3) |
| G7 | pos_source ∈ INFORMED_POS_SOURCES i NIE pos_from_store | `pos_not_informed:<src>` / `pos_from_store` | nigdy blind/center; store-replay ≠ żywy fix (Z-06) |
| G8 | brak late-pickup: `pickup_extension_redirect` is None, metrics bez `late_pickup_committed_breach` / `new_pickup_needs_extension` | `late_pickup_*` | tier>0 = propozycja przedłużenia czasu — wymaga człowieka (Adrian 31.05: Ziomek nie nadpisuje czasu sam) |
| G9 | brak ryzyka R6: `best_effort_r6_redirect` is None, `commit_divergence_redirect` is None, best nie best_effort, plan sla_violations==0 | `r6_redirect` / `commit_divergence` / `best_effort` / `plan_sla_violations` | dwie nienaruszalne reguły egzekwowane na finalnym zwycięzcy |
| G10 | pool_feasible ≥ `AUTO_ASSIGN_MIN_POOL_FEASIBLE` (3) | `scarcity_pool:<n>` | scarcity floty = wybory wymuszone (SEL-01/FEAS-02: 57-60% cross-zwycięzców to scarcity) — człowiek decyduje |
| G11 | score ≤ `AUTO_ASSIGN_SCORE_DISTRUST_CEILING` (90) | `score_distrust_ceiling:<score>` | **Bartek 2.0 §4.1: score>90 → breach 13,5-18% (korelacja się ODWRACA, inflacja R4)** — najwyżej punktowane decyzje są empirycznie najgorsze; sufit do re-oceny w E7 po capie R4 |

Bramki STANOWE (nie wchodzą do `would_auto_assign` — patrz §5): rate-cap,
cooldown po PANEL_OVERRIDE, killswitch. Powód: w shadow nic się nie wykonuje,
więc licznik „N auto/h" byłby fikcją zależną od samej telemetrii; kalibracja E7
potrzebuje czystego podzbioru jakościowego, bezpieczniki egzekucyjne nakłada
egzekutor w chwili wykonania.

## 4. Konfrontacja z danymi (wymóg promptu)

**Bartek 2.0 („score nie przewiduje wyniku"):** dlatego bramka NIE jest „wysoki
score = auto". Składowe: (a) margin Z-10 na FINALNYM rankingu (stary margin
zawyżał o medianę 105 pkt; best≠score-top w 68% decyzji — C7 to odcina),
(b) sufit nieufności G11 odcina strefę score>90, gdzie breach ROŚNIE,
(c) wyłączenia kontekstowe (HIGH_RISK 14-17 = strefa śmierci wg raportu §3.1,
scarcity, late-pickup) zamiast zaufania liczbie. AUTO historycznie najgorszą
klasą (zgodność 13%, breach 10,1%) BO margin był fikcją — Z-10 to prerequisite,
jest w kodzie od 10.06.

**panel_agree_baseline (18% / 64,3% bez explicit-reject; live od 10.06: 27,5%):**
AUTO ma celować w podzbiór o WYSOKIM acceptance. Baseline per tier: std+ 25,3%
> std 17,1% > gold 12,7% (anomalia gold do wyjaśnienia w E7). T1 whitelist
gold/std+ jest częściowo sprzeczna z tym rozkładem — **decyzja kalibracyjna E7:
czy T1-AUTO startuje jako std+/gold czy std+/std.** Telemetria
`auto_block_reasons` + PANEL_AGREE per podzbiór `would_auto_assign=true` da
odpowiedź wprost: mierzymy acceptance W PODZBIORZE would_auto przed flipem.

**Bramki flipu (wzorzec Fazy 7, do ACK przy flipie):**
1. ≥200 decyzji z `would_auto_assign=true` w shadow,
2. PANEL_AGREE acceptance w tym podzbiorze ≥75%,
3. 0 incydentów / 0 naruszeń dwóch twardych reguł w podzbiorze przez 24h,
4. KOORD-rate i acceptance całości bez regresji (lekcja #188: bramki progowe
   replayować RAZEM z deltami — tu brak delt score, ale zasada czujności zostaje),
5. flip = `ENABLE_AUTO_ASSIGN=true` w flags.json (hot) + rampa: najpierw
   sloty niskiego ryzyka (off-peak), dzienny KPI w briefingu, stop-loss:
   breach klasy AUTO > breach ACK przez 3 dni = pauza (raport B2 §11.5).

## 5. Egzekutor (szkielet, OFF)

`auto_assign_executor.maybe_execute(record, result, payload, now, assign_runner, notifier)`:

1. `C.decision_flag("ENABLE_AUTO_ASSIGN")` False → **return None** (zero pracy, zero I/O).
2. `result.would_auto_assign` musi być True; `record["verdict"]=="PROPOSE"`
   (po suppressach firmowych — hook jest PO finalnej mutacji rekordu).
3. **Rate-cap:** stan w `dispatch_state/auto_assign_state.json` (lista ts
   wykonań); wykonania w ostatnich 3600s ≥ `AUTO_ASSIGN_MAX_PER_HOUR` (6) → blok.
4. **Cooldown po PANEL_OVERRIDE:** tail-scan `learning_log.jsonl` (ostatnie
   256 KB, wzorzec `_check_panel_agree`): PANEL_OVERRIDE z proposed/actual
   == cid besta młodszy niż `AUTO_ASSIGN_OVERRIDE_COOLDOWN_MIN` (60 min) → blok
   (koordynator właśnie powiedział „nie temu kurierowi" — nie wciskamy go znowu).
5. **Wykonanie:** wybrany mechanizm = **subprocess `scripts/gastro_assign.py`**
   (identyczna ścieżka jak ASSIGN_DIRECT z Telegrama — jedyna przetestowana
   bojowo; bez importu telegram_approver, bez dotykania demona).
   `--id <oid> --kurier <best.name> --time <max(0, target_pickup_at − now)>`.
   ⚠ Ryzyko E2E: dopasowanie nazwy kuriera w panelu gastro (gastro_assign
   matchuje po nazwie) — **realny assign nigdy nie przeszedł E2E**; pierwszy
   test wykonania = osobny krok z Adrianem w dzień, na zleceniu kontrolowanym.
6. **Post-hoc TG:** `telegram_utils.send_admin_alert` („🤖 AUTO przypisał…",
   szczegóły: oid, kurier, score/margin, czas) — informacja, nie pytanie.
   Propozycja do koordynatora i tak wychodzi normalną ścieżką telegram_approver
   (przy flipie do rozstrzygnięcia: marker „wykonane" w propozycji — wymaga
   zmiany telegram_approver, POZA zakresem tej sesji, odnotowane na flip).
7. Ślad: wpis `AUTO_ASSIGN_EXECUTED` do learning_log (schema jak PANEL_AGREE)
   + aktualizacja state file. Fail-safe: każdy wyjątek połknięty z WARN.
8. Obrona przed testami: writer state/learning_log odmawia pod
   `PYTEST_CURRENT_TEST` (klasa lekcji #75/#180); default runner subprocess
   również. Testy wstrzykują `assign_runner`/`notifier`/ścieżki.

## 6. Flagi / stałe (kanon ETAP4)

- `ENABLE_AUTO_ASSIGN` → `ETAP4_DECISION_FLAGS` + `flags.json = false`
  (conftest izoluje automatycznie — czyta listę z common).
- Stałe (module-default w common.py, nadpisywalne hot przez
  `FLAGS_JSON_NUMERIC_OVERRIDES`): `AUTO_ASSIGN_MIN_POOL_FEASIBLE=3`,
  `AUTO_ASSIGN_SCORE_DISTRUST_CEILING=90.0`, `AUTO_ASSIGN_MAX_PER_HOUR=6`,
  `AUTO_ASSIGN_OVERRIDE_COOLDOWN_MIN=60.0`.
- Stare `AUTO_APPROVE_*` (martwe placeholdery) — usunięte z komentarzem
  wskazującym na AUTON-01 (zero call-site, zweryfikowane także w testach).

## 7. Czego ta zmiana NIE robi

Zero zmiany zachowania przy OFF: gate jest czystą telemetrią (nie dotyka
verdict/score/selection), egzekutor wraca po pierwszej linii. Telegram_approver
nieczytany/niezmieniany (nowe pola w shadow_decisions są addytywne — tailer
czyta tylko znane klucze). Tor panelowy, czasówki (decyzyjnie), Lokalka — nietknięte.

## 8. Rollback

- Telemetria: pola po prostu przestają być pisane po `git revert` + restart;
  do tego czasu są neutralne.
- Egzekutor: `ENABLE_AUTO_ASSIGN=false` w flags.json (hot, killswitch) —
  stan domyślny; twardy rollback = revert tagu `auton01-shadow-2026-06-13`.

---

## 9. FOLLOW-UP (ta sama noc, druga sesja): lekcja #188 w bramce — G11 ex-delta + G12

**Korekta do §4 pkt 4** („tu brak delt score") — to było nieścisłe: delty rankingowe
SĄ w surowym `best.score` i w marginie klasyfikatora, więc dotykały kryteriów AUTO
w obu kierunkach:
- kara sync −150 na best potrafiła **OTWORZYĆ** sufit G11 (jakość 200 → surowy 50 → pass);
- kara sync na runner-upie sztucznie **ROZDYMAŁA** margin klasyfikatora (60−(−95)=155
  zamiast jakościowych 5) → C2 przepuszczał.

**Fix (commit follow-up po `a7efd21`):**
- **G11** liczony na score jakościowym = reuse `dispatch_pipeline.
  _gate_score_excluding_ranking_deltas` (kanon z `30a01d2`; lazy import, fail-soft
  do surowego). Delty są ujemne ⇒ jakość ≥ surowy ⇒ test na jakości domyka oba kierunki.
- **G12 (nowa)**: margin jakościowy w semantyce Z-10 — `quality(best) − max(quality
  reszty MAYBE)` < próg bazowy poziomu (T1=15, bez bumpa HIGH_RISK — bump zaostrza
  tylko G2) → blok `margin_ex_delta`. Bez listy kandydatów → fallback na margin
  z `auto_route_context` vs ten sam próg (`margin_ex_delta_ctx`).
- Kierunek „kara na best ZAMYKA AUTO przez G2" zostaje świadomie (fail-closed,
  bezpieczna strona); recompute klasyfikatora na score ex-delta = decyzja E7.

**Plan replayu pre-flip (wymóg #188 — symulacja bramek progowych, nie tylko selekcji):**
1. Offline przebieg `evaluate_auto_assign` po ≥7 dniach shadow_decisions
   (rekonstrukcja best/kandydatów z rekordu; pola `bonus_*_shadow_delta` są
   serializowane — wystarczają do odtworzenia score jakościowego).
2. Dwa przebiegi: flagi delt ON vs OFF — zbiór `would_auto` MUSI być identyczny
   (inwariancja na delty). Różnica = regresja klasy #188, stop.
3. Acceptance PANEL_AGREE w podzbiorze `would_auto` + rozkład `auto_block_reasons`
   (per bramka) — baza: `eod_drafts/2026-06-13/AUTON01_ACCEPTANCE_SEGMENTS.md`
   (12.06: route=AUTO acceptance 27%, pełny stos przepuszcza ~0 decyzji/2d —
   progi flipu §4 wymagają rekalibracji w E7 zanim w ogóle będzie co flipować).
4. Symulacja bramek progowych egzekutora: rate-cap/cooldown na osi czasu replayu
   (ile wykonań/h by realnie wyszło), nie tylko statyczne would_auto.
