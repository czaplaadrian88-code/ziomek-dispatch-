# WERDYKT KOŃCOWY — no_gps fikcja: pomiar odwraca plan (2026-06-22)

**Read-only. ZERO prod-touch.** Harnessy: `pos_source_coverage_diag.py`, `nogps_recoverability.py`, `nogps_recoverability_v2_activity.py`, `no_gps_alwayspropose_forward_replay.py`.

## Droga
1. Diagnoza: B3 trial n=0 = rescue wpięty w gałąź wyłączoną przez ALWAYS-PROPOSE (martwy kod).
2. Plan A: dołóż karę +12 do toru ALWAYS-PROPOSE. Replay: slice 201 (8,1%), on-time 52%, kara>35 → 93% late. Wyglądało warto.
3. Korekta Adriana: „Ziomek ma przewidywać pozycję bez-GPS z ostatniej aktywności (gdzie był/odbierał/doręczał), nie karać fikcję".
4. Pomiar pokrycia → **odwrócił wniosek**.

## Pomiar (żywy log + eta_calibration_log)
- Łańcuch pozycji DZIAŁA: **88,7% decyzji = kotwica z ostatniej aktywności** (gps/last_delivered/last_picked_up/last_assigned). Czysta fikcja-centrum = **7,0% (202)**.
- Odzyskiwalność fikcji z REALNEJ aktywności kuriera:
  - realna aktywność ≤60 min przed momentem fikcji: **15 (7,4%)**
  - **>2 h temu: 184 (91,1%)**
  - kurierzy „nigdy nieaktywni": **0**
- Dominanci: 518 Rogucki (43 fikcji / 277 zdarzeń), 123 koordynator (46 / 1624), 376 (35 / 440) — wszyscy realnie aktywni, ale w momencie fikcji **bezczynni + bez GPS**.

## Wniosek — A NIE jest warte; rozszerzenie kotwicy też nie
- Fikcja no_gps = **bezczynni kurierzy bez żywego GPS** (pusty worek, ostatnia aktywność >2 h temu), nie zgubiona świeża pozycja.
- **Kara +12** tylko pesymizuje ETA tych propozycji — nie naprawia źródła (brak pozycji).
- **Wydłużenie TTL/okna kotwicy** zakotwiczyłoby ich do pozycji sprzed >2 h = zgadywanie (kurier mógł pojechać gdziekolwiek), często GORSZE niż uczciwa fikcja. 91% przypadków nie ma świeżej kotwicy do użycia.
- **Realna dźwignia = operacyjna: żywy GPS u bezczynnych kurierów** (518 Rogucki na czele) — dokładnie wniosek z lejka KOORD 20.06. To nie jest problem algorytmu.

## Opcjonalny mikro-ruch (mały, bezpieczny, marginalny)
TTL kotwicy 25→60 min odzyskałby **~13/202 (≤7%)** przypadków z aktywnością 25–60 min — ale ze świadomością, że kotwica 45–60 min dla bezczynnego kuriera bywa nieświeża. Wartość mała, ryzyko niskie, mierzalne. NIE priorytet.

## Decyzja
**Rekomendacja: NIE budować A ani rozszerzenia kotwicy.** Fikcja no_gps to objaw braku GPS u bezczynnych kurierów — robota operacyjna (GPS/apka), nie silnik. B3 trial + flagę `ENABLE_NO_GPS_UNCERTAINTY_PENALTY` warto wyłączyć/posprzątać (myli, że coś robi). Higiena: skasować zbędne at-joby trialu.

**Nic nie flipnięte. Nic nie wdrożone.** Wszystkie harnessy w `eod_drafts/2026-06-22/`.
