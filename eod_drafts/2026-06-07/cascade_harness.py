#!/usr/bin/env python3
"""Cascade replay harness — instrument do oceny KAŻDEJ polityki selekcji Ziomka.
Per-order/choice-matching MYLI przy sprzężeniu zwrotnym (worek rośnie od własnych
przypisań). Ten harness symuluje autonomiczne przypisania na strumieniu zleceń z
propagacją worka i liczy oczekiwany breach R6. Read-only.
Źródła: events.db audit_log (realne odbiory/dostawy/przypisania) + shadow_decisions
(propozycje Ziomka). Model P(breach|worek,peak) kalibrowany na czystych dostawach.
Wynik 2026-06-07 (n=2917, 24.05-06.06):
  człowiek realny 7.9% | Ziomek argmax 17.0% (worek max 33) | +anti-overload 15.1%
  | load-aware(kandydaci) 13.6% | load-aware(pełny roster) 10.1% (worek max 3)
Wniosek: progresja = load-aware distribution + pełny roster. Anti-stack/un-demote odrzucone.
Uwaga: model kapuje worek na 4 -> 'auto' to DOLNA granica. AUC modelu 0.57 (worek/peak
to słaby predyktor; reszta breachu = geometria/prep/ruch).
"""
import json, sqlite3, statistics as st
from datetime import datetime, timezone, timedelta
from collections import defaultdict, Counter
WAR=timezone(timedelta(hours=2)); DB='/root/.openclaw/workspace/dispatch_state/events.db'
SD=['/root/.openclaw/workspace/scripts/logs/shadow_decisions.jsonl','/root/.openclaw/workspace/scripts/logs/shadow_decisions.jsonl.1']
def tw(i):
    try: return datetime.fromisoformat(i.replace('Z','+00:00')).astimezone(WAR).replace(tzinfo=None)
    except: return None
def peak(d):
    w,h=d.weekday(),d.hour
    return ((11<=h<14)or(17<=h<20)) if w<=4 else (16<=h<21 if w==5 else False)
# ... (pełna logika jak w sesji 2026-06-07: build ev/pick/deliv/assigns, concur, bagt,
#      model pr(bag,peak), props z shadow, sim(policy in {auto,antiover,
#      loadaware_cands,loadaware_all}). Zob. werdykt [[ziomek-autonomy-cascade-verdict]].)
print("Patrz docstring: harness odtworzony w sesji 2026-06-07; pełna implementacja w transkrypcie.")
