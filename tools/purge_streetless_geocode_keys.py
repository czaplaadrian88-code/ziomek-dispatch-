#!/usr/bin/env python3
"""Purge geo-poison: usuń z geocode_cache.json wpisy, których KLUCZ nie identyfikuje
ulicy. To pozostałości buga `m`-eating-M-streets (2026-06-08) + śmieci wejściowe
(numery telefonów w polu adresu). „Magazynowa 3"/„Malachitowa 3" itp. miały klucz
zredukowany do samego numeru/miasta → kolizja → cudze współrzędne.

Detekcja (bezpieczna dla wsi gdzie nazwa wsi = ulica, np. „Olmonty 71",
„Horodniany 53"): klucz MA ulicę, jeśli którykolwiek segment (po przecinku)
zawiera JEDNOCZEŚNIE token-słowo ≥3 liter (≠ „białystok") ORAZ token z cyfrą
(numer domu). Inaczej = poison.

Po fixie regexu nowy normalizer i tak nie generuje już tych kluczy (zostają
osierocone), ale usuwamy je, by NIGDY nie mogły zwrócić cudzych koordów.
Re-geokod ze stringu „original" odtwarza poprawne współrzędne (Google primary).

URUCHAMIAĆ PO restart dispatch-shadow + dispatch-panel-watcher. Domyślnie DRY-RUN.
"""
import json
import os
import re
import sys
import tempfile
import time
from pathlib import Path

CACHE = Path("/root/.openclaw/workspace/dispatch_state/geocode_cache.json")
CITY = {"białystok", "bialystok"}
_POST = re.compile(r"\b\d{2}-?\d{3}\b")
_ALPHA = re.compile(r"[^a-ząćęłńóśźż]")


def has_street(key: str) -> bool:
    """True jeśli któryś segment ma JEDNOCZEŚNIE słowo-ulicę (≥3 liter, ≠ miasto)
    ORAZ token z cyfrą (numer domu) — czyli identyfikuje konkretny adres uliczny.
    Bezpieczne dla wsi-jako-ulica („Olmonty 71") i formatu „kod miasto, ulica nr"."""
    for seg in key.lower().split(","):
        seg = _POST.sub(" ", seg)
        seg = re.sub(r"\(.*?\)", " ", seg)
        toks = [t for t in re.split(r"[\s/]+", seg) if t]
        has_word = any(
            len(_ALPHA.sub("", t)) >= 3 and _ALPHA.sub("", t) not in CITY
            for t in toks
        )
        has_num = any(re.search(r"\d", t) for t in toks)
        if has_word and has_num:
            return True
    return False


def has_house_number(key: str) -> bool:
    """True jeśli w kluczu jest numer domu (cyfra po usunięciu kodu pocztowego)."""
    return bool(re.search(r"\d", _POST.sub(" ", key)))


def main(apply: bool) -> int:
    data = json.loads(CACHE.read_text())
    # Poison kolizyjny = JEST numer domu ale BRAK nazwy ulicy → „Magazynowa 3" i
    # „Malachitowa 3" zlewają się w „3". Wpisy bez numeru (sama ulica „Sienkiewicza")
    # NIE kolidują między ulicami — zostawiamy je (osobny temat: adres bez numeru).
    bad = {
        k: v for k, v in data.items()
        if has_house_number(k) and not has_street(k)
    }
    print(f"cache entries total: {len(data)}")
    print(f"streetless (poisoned/garbage) keys: {len(bad)}")
    for k, v in sorted(bad.items()):
        print(f"  PURGE {k!r:30} <- {v.get('original', '')!r}")
    if not apply:
        print("\nDRY-RUN — nic nie zapisano. Dodaj --apply aby usunąć.")
        return 0
    if not bad:
        print("nic do usunięcia.")
        return 0
    backup = CACHE.with_suffix(f".json.bak-pre-streetless-purge2-{int(time.time())}")
    backup.write_text(CACHE.read_text())
    print(f"\nbackup: {backup}")
    cleaned = {k: v for k, v in data.items() if k not in bad}
    fd, tmp = tempfile.mkstemp(dir=str(CACHE.parent), prefix=f".{CACHE.name}.tmp-", suffix=".json")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(cleaned, f, ensure_ascii=False, indent=2)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, CACHE)
    print(f"removed {len(bad)} keys; cache now {len(cleaned)} entries.")
    return 0


if __name__ == "__main__":
    sys.exit(main(apply="--apply" in sys.argv))
