#!/bin/bash
# At-job reminder (2026-05-24): weryfikacja po kilku dniach zmian scoringu korytarza Ziomka.
# Reguła "Dwudniowa/kilkudniowa walidacja" (feedback_rules.md) — pilnowanie werdyktu.
export PYTHONPATH=/root/.openclaw/workspace/scripts
export MSG='⏰ Ziomek — przegląd zmian dispatchu z 24.05 (po kilku dniach)

LIVE od 24.05: F2 (kierunkowość tylko na otwartych dropach fali), F4 (słaby pick → ALERT), F5 (zakaz powrotu do tej samej restauracji).
OFF, czeka na te dane: F1 (gradient kary korytarza).

Co masz zrobić: odpal sesję Claude Code i poproś o „przegląd dispatchu 24.05 z historii". Zrobię na learning_log vs korpus eod_drafts/2026-05-24/ziomek_bad_picks_corpus.md:
1. Werdykt F2/F4/F5 — lepiej czy nie (czy -35 zniknęło z dobrych bundli, czy złe dostają mocniej, override-rate, czy anti-patterny zniknęły). keep albo flaga OFF.
2. Aktywacja/recalibracja F1 (gradient) na czystym wave-scoped cosine — czy nie osłabi 472338.
3. Kalibracja F6 (kary R5 detour / R8 span za zygzak pickupów) na danych.
4. Decyzja F3.'
/root/.openclaw/venvs/dispatch/bin/python -c "import os; from dispatch_v2 import telegram_utils; telegram_utils.send_admin_alert(os.environ['MSG'])" 2>&1 | logger -t corridor_verify_reminder
