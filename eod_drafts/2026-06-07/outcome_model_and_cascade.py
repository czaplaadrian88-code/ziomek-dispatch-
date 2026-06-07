#!/usr/bin/env python3
"""Wzmocniony model outcome (breach R6) + cascade policy ladder. READ-ONLY/offline.
Uruchom venvem z numpy/sklearn:
  /root/.openclaw/workspace/scripts/ml_data_prep/venv/bin/python3 outcome_model_and_cascade.py

Wynik 2026-06-07 (n_train 3172, dni do 06.06):
  AUC: bag+peak 0.51 -> geometria 0.61 -> PELNY 0.64
  Sterowniki: restrate +0.054 == dist +0.054 >> hour/bag (~0.017) >> tier/peak/dctr ~0
  => breach = trudnosc zlecenia (prep restauracji + dystans), NIE obciazenie kuriera.
  Drabina (mocny model, n=2882): czlowiek 7.9% | argmax 11.3% (worek 33!) |
    +anti-overload 10.9% | load-aware kandydaci 9.9% | load-aware+PELNY ROSTER 8.0% (worek<=3).
  Argmax 11.3% = DOLNA granica (model ekstrapoluje poza wsparcie przy worku 33; realnie gorzej).
  load-aware+roster 8.0% = WIARYGODNE (worki <=3, w-wsparciu). PARYTET z czlowiekiem.

KLUCZ geokodowania (wczesniejszy blad): cache key = 'ulica numer, bialystok' (bez mieszkania),
shadow.address_id = adres KLIENTA (NIE restauracji); pickup coords = restaurant_coords po NAZWIE.
"""
import json, sqlite3, math, glob, re
import numpy as np
from datetime import datetime, timezone, timedelta
from collections import defaultdict, Counter
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.metrics import roc_auc_score
from sklearn.inspection import permutation_importance

WAR=timezone(timedelta(hours=2)); CENTER=(53.1325,23.1688)
DB='/root/.openclaw/workspace/dispatch_state/events.db'
GEOC='/root/.openclaw/workspace/dispatch_state/geocode_cache.json'
RESTC='/root/.openclaw/workspace/dispatch_state/restaurant_coords.json'
SD=['/root/.openclaw/workspace/scripts/logs/shadow_decisions.jsonl',
    '/root/.openclaw/workspace/scripts/logs/shadow_decisions.jsonl.1']

def tw(i):
    try: return datetime.fromisoformat(i.replace('Z','+00:00')).astimezone(WAR).replace(tzinfo=None)
    except: return None
def hav(a,b):
    if not a or not b or a!=a or b!=b: return np.nan
    (la1,lo1),(la2,lo2)=a,b; p=math.pi/180
    x=math.sin((la2-la1)*p/2)**2+math.cos(la1*p)*math.cos(la2*p)*math.sin((lo2-lo1)*p/2)**2
    return 2*6371*math.asin(math.sqrt(x))
def peak(d):
    w,h=d.weekday(),d.hour
    return 1 if (((11<=h<14)or(17<=h<20)) if w<=4 else (16<=h<21 if w==5 else False)) else 0
def na(a):  # delivery addr -> cache key
    a=(a or '').strip().lower().split('/')[0].strip()
    a=re.sub(r'\s+\d+$','',a).strip() if re.search(r'\d+\s+\d+$',a) else a
    return a+', białystok'

# --- patrz pełna implementacja w sesji 2026-06-07 / werdykt [[ziomek-autonomy-cascade-verdict]] ---
# (loader geo/restname/meta/tiers; audit_log -> pick/deliv/concur/bagt; shadow -> omar/props;
#  rows -> features [bag,hour,peak,dist,dctr,tier,prep,restrate]; HGB train/test split czasowy;
#  AUC + permutation_importance + kalibracja; cascade sim policy in
#  {auto, antiover, la_cands, la_all} z propagacja worka (occupancy = mediana assign->deliver).)
print("Zob. docstring + [[ziomek-autonomy-cascade-verdict]]. Pełny kod w transkrypcie sesji 2026-06-07.")
