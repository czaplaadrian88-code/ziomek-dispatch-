# C2 post-flip monitor — pierwszy peak (2026-05-30)

- Flip: `ENABLE_C2_NEG_GAP_DECAY=1` LIVE 2026-05-29 20:35 UTC.
- Okno (ts ≥): `2026-05-29T20:35:00+00:00`
- Decyzji z feasible≥1: **88**
- Decyzji gdzie C2 zmienił jakikolwiek score: **41**
- Score-argmax flipów (ON vs OFF): **0**
- Klasy: `{}` | Severity: `{}`

**Werdykt:** 🟢 GREEN: 0 score-argmax flipów C2 w oknie

_Brak flipów w oknie._

---
Metoda: base-subtraction + realna `bug2_wave_continuation_bonus` (flaga w pamięci, Lekcja #151). RED = AUTO flip gdzie zdemotowany pick był faktycznym dostawcą. Read-only — bez wpływu na prod.
