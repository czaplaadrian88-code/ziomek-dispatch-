# R-04 ENFORCE — re-audyt 12/13.06.2026 (nocna sesja, STRETCH 1)

**Werdykt: NIE FLIPOWAĆ `ENABLE_R04_ENFORCE` (zostaje OFF).** Tryb suggest-only działa
poprawnie i ma pełne dane dostawowe, ale enforce w obecnym schemacie to
**jednokierunkowa zapadka promocji bez żadnego żywego sygnału negatywnego** — jeden
z dwóch kandydatów do promocji ma breach 2× mediana floty. Flip = za ACK Adriana,
po remapie sygnału negatywnego w E7 (at#131 17.06), nie wcześniej.

---

## 1. Kompletność okna 30 dni — ZWERYFIKOWANA, wynik mieszany

| Źródło | Pokrycie na 12.06 19:30 UTC | Zasila | Status |
|---|---|---|---|
| `events.db:audit_log` (COURIER_PICKED_UP/DELIVERED, retention 90d) | **14.05 → 12.06, 30 dni, codziennie, bez dziur** (7 078 dostaw w oknie; 143-386/d) | peak_deliveries / peak_active_days / peak_speed / completeness — czyli WSZYSTKIE bramki prędkościowe | ✅ pełne |
| `learning_log.jsonl` (+ `.1`) | **tylko 01.06 → 12.06 (~11,7 dnia)** — żywy od 08.06, `.1` = 01-07.06, starszych rotacji NIE MA na dysku (zero `.2.gz`; logrotate `rotate 30` + `size 100M` rotuje ~co tydzień, a archiwum sprzed 01.06 nie istnieje) | wyłącznie `tg_negative_30d` | ⚠ okno ~12d, ale patrz niżej — metryka i tak MARTWA |

Wniosek: założenie z promptu „baza ma ~pełne 30 dni" jest **prawdziwe dla metryk
dostawowych** (audit_log, główny napęd schematu), **nieprawdziwe dla learning_log** —
co nie zmienia wyniku, bo jedyna metryka z learning_log jest martwa (0 wszędzie;
znalezisko e7-rotated-readers 11.06: akcje INNY/TG_REASON = 0 w 23,4k rekordów).
Run evaluatora z 01:00 UTC dziś (timer `dispatch-r04-evaluator`) użył rotated-readers
i pełnego okna — metryki typu 278 peak-dostaw/30d to potwierdzają.

## 2. Co by zmienił enforce DZIŚ (offline, `r04_apply._build_eligible_changes`, zero TG)

Na 53 kurierów w `tier_suggestions.json` (gen. 2026-06-12T01:00Z):

- **eligible: 2** — obie PROMOCJE std → std+:
  - **cid 409 Mateusz Bro** — peak 76 dostaw/8 dni, speed med 16,67 / p25 10,39, completeness 96,1%, **breach 5,2%** (A2, n=118, confidence high, reliability 0,871). Mediana floty breach = 6,6%. **Kandydat mocny.**
  - **cid 471 Łukasz W** — peak 92 dostaw/8 dni, speed med 16,48 / p25 10,54, completeness 96,7%, ale **breach 13,0%** (A2, n=46, confidence high, reliability 0,804) = **2× mediana floty**. Przechodzi bramki, bo jedyny negatywny gate (`tg_negative ≤5`) jest trywialnie spełniony przez martwą metrykę. **Kandydat wątpliwy.**
- skip_match: 51 (w tym 28 insufficient_data → fail-safe „keep current" → match) · skip_cooldown/gold/unsupported: 0
- **demotions: 0 — i to STRUKTURALNIE, nie empirycznie:**
  - std→slow: jedyna reguła to `tg_negative>5 sustained 14d` — metryka martwa → **nigdy nie odpali**;
  - std+→std: `speed>19 sustained 14d` (sustained_days SUPPRESSED w r04_apply Phase 1+2 — brak historii evolution) LUB `tg_negative>8` (martwa) → **nigdy nie odpali**;
  - gold: demotion też tylko sustained/tg_negative → j.w.
- gold_candidate (advisory, bez enforcement): tylko cid 123 — patrz side-finding §4.

Ekspozycja kandydatów w żywych decyzjach (PROPOSE od 05.06, n=1486): 409 wygrywa 72×
(4,8%), 471 — 25× (1,7%). Promocja zmienia im tier-multipliers (SPEED/DWELL_BY_TIER,
kalibracja 10.06) i wpuszcza do whitelisty T1 AUTO.

## 3. Dlaczego NIE flipować teraz (uzasadnienie werdyktu)

1. **Brak żywego sygnału negatywnego = zapadka.** Enforce może dziś TYLKO promować.
   Kurier raz promowany nie zostanie nigdy zdemotowany automatem (martwa metryka +
   suppressed sustained). Asymetria rośnie z każdym dniem działania.
2. **Case 471 to dowód, nie hipoteza:** breach 13% przy n=46 przechodzi wszystkie bramki.
   Żywy odpowiednik sygnału negatywnego istnieje (PANEL_OVERRIDE 1490/30d, A2 breach_rate
   z confidence) — remap to decyzja E7 (at#131 17.06), już zaplanowana.
3. **Interakcja z AUTON-01 (budowany dziś za flagą):** std+ ∈ whitelisty T1 klasyfikatora
   AUTO. Flip enforce automatycznie poszerzałby przyszłą populację auto-assign o świeżo
   promowanych — w tym 471. Sekwencja musi być: E7 remap sygnału → enforce → dopiero
   potem szersze AUTO.
4. **Dwóch pisarzy courier_tiers.json:** panel FLT-04 (ręczne reklasyfikacje Adriana,
   `d356f02..2a8ae54`) i r04_apply. Oba atomic-write, ale flip enforce bez uzgodnienia
   = automat może nadpisać/odwrócić ręczną decyzję Adriana z panelu (cooldown 7d chroni
   tylko przed własnymi zmianami automatu — wpisy panelu nie trafiają do tier_evolution).

## 4. Side-findingi (poza zakresem werdyktu, do wiadomości)

- **cid 123 „Bartek O." (gold, jedyny gold_candidate):** wg raportu Bartek 2.0 ostatnia
  dostawa Bartka O. = 19.04 i nie ma go w `grafik_full_names.json` ani `kurier_ids.json`,
  a **audit_log pokazuje 23-36 dostaw/dzień na cid 123 do dziś** (264 peak/30d). Albo
  konto panelowe jest reużywane przez inną osobę (klasa #187 — GPS z cudzego telefonu;
  #177 — kanon nazw po cid), albo teza raportu B2 o odejściu jest błędna. Jeśli reuse —
  tier GOLD przypisany osobie, której nikt nie ocenił. **Do potwierdzenia u Adriana.**
- 28/53 kurierów insufficient_data (głównie 0 peak-dostaw w 30d — nieaktywni); fail-safe
  „keep current" działa poprawnie.
- Schema peak_window 11-13/17-19 = zgodny z doktryną PEAKWIN 11-14/17-20 (godziny
  graniczne 14/20 to początek kolejnego slotu — OK).

## 5. Rekomendacje (do ACK Adriana, NIE wykonane tej nocy)

1. `ENABLE_R04_ENFORCE` **zostaje OFF** (stan obecny — nic nie zmieniono).
2. Promocja **409 Mateusz Bro → std+ ręcznie w panelu FLT-04** — dane mocne (szybszy
   niż obecny benchmark std+ 509 Dariusz M, breach poniżej mediany floty).
3. **471 Łukasz W — wstrzymać** do spadku breach (<8% przy n≥60) albo do E7-remapu.
4. W E7 (at#131): remap `tg_negative_30d` → PANEL_OVERRIDE-rate lub A2 `breach_rate_loo`
   (confidence-gated) we WSZYSTKICH bramkach schematu; odblokować sustained_days
   (historia evolution istnieje od 01.05); dopiero potem rozmowa o enforce.
5. Wpisy panelu FLT-04 do `tier_evolution.jsonl` (jedna linijka w handlerze panelu) —
   żeby cooldown automatu widział ręczne decyzje.

---
*Metodologia: `_build_eligible_changes` offline (zero Telegrama, zero zapisu), audit_log
per-dzień 14.05-12.06, A2 = `courier_reliability.json` (gen. z backfill outcomes n=3666),
ekspozycja = shadow_decisions od 05.06. Żadna flaga/plik stanu nie zostały zmienione.*
