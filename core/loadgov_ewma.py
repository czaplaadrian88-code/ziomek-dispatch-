"""G5 — KANONICZNY jądro-krok EWMA load governora (jedno miejsce, dwie serie).

Po co osobny moduł: w silniku żyją DWIE serie tej samej rodziny, mierzące DWIE
RÓŻNE wielkości, i każda ma swojego jedynego właściciela:

  * `dispatch_pipeline._loadgov_compute` — seria LEGACY nad mianownikiem
    „flota dispatchowalna" (`len(fleet_snapshot)`); zasila ŻYWĄ politykę
    `ENABLE_FLEET_LOAD_GOVERNOR` (kara worka ≥3, alert trybu defensywnego).
    Jej progi 2,7 / 3,5 / 3,0 były kalibrowane DOKŁADNIE na tym mianowniku,
    więc nie wolno go ruszyć bez rekalibracji i ACK ownera;
  * `core.loadgov_publisher` — seria EQUAL-TREATMENT nad mianownikiem
    obejmującym RÓWNO kurierów z GPS i bez (RUN3-b sekcja 3, G5); zasila
    wyłącznie snapshot czytany przez `core.loadgov_snapshot`.

Wspólny jest wyłącznie RACHUNEK. Gdyby każda seria liczyła alfę u siebie,
powstałyby dwie kopie tej samej polityki wygładzania i mogłyby się rozjechać
przy pierwszej zmianie tau — dlatego krok jest TU, a serie trzymają tylko swój
stan. Moduł jest CZYSTY: bez stanu, bez I/O, bez flag.
"""
from __future__ import annotations

import math
from datetime import datetime
from typing import Optional


def ewma_step(prev: Optional[float], prev_ts: Optional[datetime],
              sample: float, now: datetime, tau_min: float) -> float:
    """Kolejna wartość EWMA o stałej czasowej `tau_min` (minuty).

    Semantyka 1:1 z inline'em SP-B2-LOADGOV z 2026-06-11 (parytet pilnowany
    testem `test_ewma_step_parity_with_frozen_legacy_math`):

      * brak poprzedniej próbki albo brak jej stempla ⇒ EWMA = próbka
        (pierwsza obserwacja nie ma czego wygładzać);
      * `alpha = 1 - exp(-dt/tau)`, dt liczone w minutach i przycięte od dołu
        do 0 (cofnięty zegar nie odwraca wygładzania), tau przycięte do 0,1
        (tau→0 dałoby dzielenie przez zero);
      * wynik zaokrąglony do 3 miejsc — TA SAMA precyzja co seria legacy, bo
        jest porównywana z progami zapisanymi z tą samą dokładnością.
    """
    if prev is None or prev_ts is None:
        return float(sample)
    dt_min = max(0.0, (now - prev_ts).total_seconds() / 60.0)
    tau = max(0.1, float(tau_min))
    alpha = 1.0 - math.exp(-dt_min / tau)
    return round(alpha * float(sample) + (1.0 - alpha) * float(prev), 3)
