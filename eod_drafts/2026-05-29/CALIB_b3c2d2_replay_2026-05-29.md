# Replay-calibration B3 / C2 / D2 — 2026-05-29

Audyt `AUDIT_ZIOMEK_2026-05-28`. Wszystkie 3 zmiany SHADOW (flagi default OFF,
pushnięte na origin: B3 `6d68105`, C2 `6664213`, D2 `d9bbffe`). Kalibracja
offline — **brak flip-flag, brak restartu** (held for explicit ACK).

- Skrypt: `tools/calib_b3c2d2_2026_05_29.py` (woła REALNĄ
  `common.bug2_wave_continuation_bonus` z flagą przełączaną w pamięci — Lekcja #151).
- Źródło: `scripts/logs/shadow_decisions.jsonl` — **1555 decyzji**, okno
  2026-05-24T06:32 → 2026-05-29T19:38 (~5.5 dnia), 8149 kandydatów.

---

## C2 — neg-gap decay `bug2_wave_continuation_bonus` (IMPACT ⟶ realny)

Delta zawsze ≤ 0. Veta (V326_WAVE_VETO / NEW_DROP / FIX_C) są niezależne od
wartości bonusu → "survived veto" (logged>0) niezmiennik względem C2, więc
delta = bonus_ON − 30 aplikuje się wprost do score.

- Kandydaci ze zmienionym score: **947** (|gap|≤10 → 0 zmiany; (10,30] → częściowy
  decay 316; [-30,-10] → 367; full−30 dla |gap|>30 → 264).
- **SCORE-ARGMAX FLIPS: 30 / 1555 = 1.9%** (zmiana zwycięzcy wśród feasible).
- **Kierunek jednorodny:** w KAŻDYM z 30 flipów OLD-winner to kandydat
  antycypacyjny (gap ujemny) tracący bonus; NEW-winner ma `c2Δ≈0` (nie polega na
  bonusie antycypacji). C2 systematycznie odsuwa "stale wave" picks ku czystszym.
- Podział jakościowy (po |gap| OLD-winnera):
  - ~13 SILNY IMPROVEMENT — |gap| ≥ ~25 (np. 476329 gap −45.6, 476859 gap −47.7,
    476203 gap −35.4): bonus +30 za pickup grubo wyprzedzający wolność kuriera =
    fałszywa fala. Usunięcie słuszne.
  - ~12 IMPROVEMENT umiarkowany — |gap| 15–25; kilka NEW-winnerów ma gap DODATNI
    (kurier wolny przed pickupem) → realnie lepszy wybór.
  - ~5 NEUTRAL/szum — |gap| ~10–14, kara <6 pkt, marginesy <2 pkt (476010 Δ−1.16
    margines 1.13; 476621 margines 0.0 tie-break). Poziom szumu.
  - **0 wyraźnych REGRESJI** (żaden NEW-winner nie jest gorszą antycypacją).
- ⚠ To GÓRNA GRANICA: score-argmax ≠ potwierdzony verdict flip. Realna selekcja ma
  warstwę ponad score (best≠max-among-feasible w 285 decyzjach) + V3.16 demote pass
  (Lekcja #150/#151) → część z 30 może być wchłonięta. Lista 30 = kandydaci do
  spot-checku przed/po flipie.

## B3 — `compute_wait_penalty` gradient (wait>60) (IMPACT ⟶ zero w oknie)

- Kandydaci z v327-wait-pen SUM ≤ −1000: **14**. **Wszystkie `feas=NO`,
  `best_effort=False`, ŻADEN nie jest `best`.** Niedopuszczone, przegrane alternatywy.
- ⟶ **B3 ranking-INERT w całym oknie.** Zero wpływu na jakąkolwiek decyzję.
- Dokładny re-compute niemożliwy z logu (suma po pickupach; per-pickup wait_min
  nie serializowany). Swing per long-pickup ∈ [−1000, +300]. Pełna wierność =
  `sequential_replay` re-sim, ale skoro klamra dotyka tylko NO-kandydatów, brak
  motywacji. Flip B3 = low-risk / low-reward (czystsza krzywa, ale klamra nie
  wiązała żadnego feasible w tym oknie).

## D2 — soft-degrade STALE schedule zamiast NO_ACTIVE_SHIFT (IMPACT ⟶ rzadki safety-net)

- Whole-fleet reject signature (`no_solo_candidates / wszyscy odrzuceni nawet
  solo`): **4 / 1555** ← górna granica powierzchni D2.
  - 475692 @ 05-24 12:23 (fleet_n=12, feas=0, KOORD)
  - **476302/476303/476304 @ 05-27 05:06–05:11 (fleet_n=15, feas=0, KOORD)** —
    klaster 3 zleceń o świcie w 5 min, cała 15-osobowa flota odrzucona nawet solo.
    Świt + masowy whole-fleet reject = silny kandydat na dokładnie ten scenariusz,
    który D2 soft-degraduje (grafik stale/missing o 05:00 → shift_end=None całej
    flocie → NO_ACTIVE_SHIFT).
- ⚠ `schedule_source_stale` NIE istnieje w historycznych logach → nie potwierdzę
  ile z 4 było STALE-induced. D2 odpala TYLKO na load-fail grafiku (rzadkie), więc
  realna częstość << 4. Pure safety-net: gdy grafik świeży → flag inert (HARD reject
  zostaje). Brak downside przy fresh.

---

## Rekomendacja (do decyzji Adriana — flip held for ACK)

| Zmiana | Wpływ w oknie | Ryzyko flipu | Sugestia |
|---|---|---|---|
| **B3** | 0 (tylko NO-kandydaci) | ~zero | Flip OK kiedykolwiek; czystsza krzywa, bez urgency |
| **C2** | 30 flipów (1.9%), kierunek poprawny, 0 regresji | umiarkowane (realne assignmenty) | Spot-check 3–4 silnych flipów (476329/476859/476203) vs realne outcome → potem flip; albo flip z monitoringiem |
| **D2** | ≤4 (góra), pure safety-net | ~zero przy fresh grafik | Flip jako safety-net; sprawdzić klaster 05-27 05:06–05:11 czy to był stale grafik |

Najsilniejszy sygnał: **C2 robi dokładnie to, co audyt zakładał** (kasuje +30 za
fałszywe fale antycypacyjne) bez widocznych regresji. B3/D2 to bezpieczne, niskie-
ryzyko domknięcia. **Nic nie flipnięte — czekam na ACK które flagi.**

---

## SPOT-CHECK 30 flipów C2 vs FAKTYCZNE outcome (events.db + snapshots) — 2026-05-29

Flagi OFF w prod==shadow → shadow `best` = realna propozycja proda. Pytanie: czy
demotowany przez C2 pick faktycznie dowoził (→ C2 regresja) czy odpadał (→ C2 OK).

**Footprint auto-pilota mikroskopijny:** z 30 flipów `auto_route` = **AUTO tylko 1**
(476608), ACK 17, ALERT 12. Czyli C2 ON zmienia realny auto-assignment w ~1 decyzji
/5.5 dnia; reszta to propozycja pod review człowieka.

**3 głębokie flipy sprawdzone imiennie — w KAŻDYM proponowany pick ≠ faktyczny kurier:**
- 476329 (ALERT): prod=514 → C2=370 → **dowiózł 393** (dostarczone 12:45 < deadline 13:08).
- 476859 (ALERT): prod=470 → C2=393 → **dowiózł 500** (pickup 13:47 ~ on-time 13:44).
- 476608 (**jedyny AUTO**): prod=400 → C2=413 → **dowiózł 123** (dostarczone 13:13 < 13:46).
  ⟶ nawet jedyny AUTO-flip nie „kleił się": prod-pick 400 nie dowoził → C2 nieszkodliwy.

**Join całej 30 do COURIER_DELIVERED (11/30 ma outcome; 19 bez — KOORD/cancel/bridge):**
- prod-proponowany kurier == faktyczny deliverer: **3 / 11** (476520→471, 476857→413, 476975→503) — wszystkie ACK/ALERT, ŻADEN AUTO.
- C2-alt kurier == faktyczny deliverer: **0 / 11**.

**Wniosek (niuansuje rekomendację C2):**
- C2 **LOW-RISK** do flipu: ~0 wpływu na AUTO; 3 „advisory regresje"/5.5d (gdzie
  demotowany pick faktycznie dowiózł) są WSZYSTKIE human-gated (ACK/ALERT).
- ALE spot-check **nie** dał pozytywnego dowodu że C2 POPRAWIA outcome (pick C2 trafił
  w faktycznego deliverera 0/11). Flipy C2 lądują na słabych/wczesnych/antycypacyjnych
  decyzjach, które i tak są downstream nadpisywane.
- Uczciwa teza: C2 = „nieszkodliwy + czystszy scoring (nie nagradza fałszywych fal)",
  NIE „mierzalnie lepsze dostawy". Flip ON + monitoring, albo dłuższy shadow jeśli
  chcemy najpierw twardy dowód outcome.
