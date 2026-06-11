"""_rotated_logs — logrotate-aware czytanie JSONL (SP-B2-LOGROT, 2026-06-11).

logrotate (/etc/logrotate.d/dispatch-v2: daily + size 100M + copytruncate +
delaycompress) truncuje żywe pliki ~co tydzień. Konsument czytający TYLKO żywy
plik widzi po rotacji wyłącznie bieżący ogon — okno agregatu jest po cichu
przycinane (incydent 2026-06-08: zamrożony feed A2/retro przez 2 dni; fix
wzorcem backfill_decisions_outcomes commit 23e64ff). Ten moduł uogólnia tamten
wzorzec dla wszystkich narzędzi w tools/ + daily_briefing.

API (konsument zachowuje SWÓJ per-rekord filtr ts — helper odsiewa tylko CAŁE
pliki, których zawartość na pewno jest starsza od okna):

  files_in_window(base, cutoff_dt=None) -> list[str]
      Chronologicznie: najstarszy zrotowany (.N najwyższy) → ... → .1 → żywy.
      Zrotowany plik pomijany, gdy jego mtime (czas rotacji = koniec
      zawartości) < cutoff_dt — cała treść starsza od okna.

  iter_jsonl_lines(base, cutoff_dt=None) -> Iterator[str]
      Surowe linie ze wszystkich plików okna; .gz otwierane transparentnie;
      plik nieczytelny (OSError) jest pomijany z notką na stderr.

  iter_jsonl_records(base, cutoff_dt=None) -> Iterator[dict]
      j.w. + json.loads per linia; linie nie-JSON / nie-dict pomijane po cichu
      (konsumenci i tak je pomijali — bez zmiany zachowania).

Testy: dispatch_v2/tests/test_b2_rotated_logs.py.
"""
from __future__ import annotations

import glob
import gzip
import json
import os
import re
import sys
from datetime import datetime, timezone
from typing import Iterator, Optional

_ROT_SUFFIX_RE = re.compile(r"\.(\d+)(\.gz)?$")


def open_maybe_gz(path: str):
    """Otwórz tekstowo, transparentnie obsługując .gz (zrotowane + skompresowane)."""
    if str(path).endswith(".gz"):
        return gzip.open(path, "rt", encoding="utf-8", errors="replace")
    return open(path, encoding="utf-8", errors="replace")


def files_in_window(base: str, cutoff_dt: Optional[datetime] = None) -> list:
    """Żywy plik + zrotowane siblingi (.1, .2.gz, ...) w oknie od cutoff_dt.

    Zwraca ścieżki CHRONOLOGICZNIE (najstarsze → żywy): konsumenci czytający
    sekwencyjnie zachowują naturalny porządek czasowy linii. mtime zrotowanego
    pliku = moment rotacji = znacznik KOŃCA jego zawartości; gdy mtime <
    cutoff_dt, cała zawartość jest starsza od okna → plik pomijany. Per-line
    filtr ts pozostaje po stronie konsumenta.
    """
    base = str(base)
    rotated = []
    for p in glob.glob(base + ".*"):
        m = _ROT_SUFFIX_RE.search(p)
        if not m:
            continue  # .lock / .bak-* / inne siblingi
        if cutoff_dt is not None:
            try:
                mt = datetime.fromtimestamp(os.path.getmtime(p), timezone.utc)
            except OSError:
                continue
            if mt < cutoff_dt:
                continue
        rotated.append((int(m.group(1)), p))
    rotated.sort(reverse=True)  # .3, .2, .1 — najstarsze najpierw
    files = [p for _, p in rotated]
    files.append(base)  # żywy zawsze ostatni (mtime = teraz)
    return files


def iter_jsonl_lines(base: str, cutoff_dt: Optional[datetime] = None) -> Iterator[str]:
    """Yield linie ze wszystkich plików okna (zrotowane chronologicznie + żywy)."""
    for p in files_in_window(base, cutoff_dt):
        if not os.path.exists(p):
            continue
        try:
            with open_maybe_gz(p) as f:
                for line in f:
                    yield line
        except OSError as e:
            sys.stderr.write(f"[_rotated_logs] pomijam {p}: {e!r}\n")
            continue


def iter_jsonl_records(base: str, cutoff_dt: Optional[datetime] = None) -> Iterator[dict]:
    """Yield dict-y z linii JSONL; linie puste / nie-JSON / nie-dict pomijane."""
    for line in iter_jsonl_lines(base, cutoff_dt):
        line = line.strip()
        if not line:
            continue
        try:
            rec = json.loads(line)
        except (json.JSONDecodeError, ValueError):
            continue
        if isinstance(rec, dict):
            yield rec
