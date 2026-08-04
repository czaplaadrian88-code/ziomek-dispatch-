"""Canonical CID-keyed courier-name roster — one validated owner (A-6/G3, K4).

Before A-6/G3 the ``{cid: name}`` roster was assembled by four byte-similar
copies of ``_load_courier_names`` (``courier_resolver``, ``courier_ranking``,
``sla_tracker``, ``telegram_approver``). Every copy keyed the roster by a raw
``str(cid)`` with NO validation, so a non-canonical value in a source file
(``True`` -> ``"True"``, ``[]`` -> ``"[]"``, ``" 17 "``, ``"017"``, ``-5``,
``0``) entered the published fleet as a bogus CID key that no canonical reader
(``manual_overrides._all_name_to_cid``, ``courier_availability._canon_cid``)
resolves to the same identity.

This module is that single validated owner. Each roster row is keyed through
:func:`identity.schema.canonical_numeric_cid` — the same canonical CID validator
the onboarding writer (G4) and the grafik writer (G7) already delegate to. A
non-canonical CID record is skipped INDIVIDUALLY (fail-soft, owner variant): the
healthy rows survive, fleet availability on the hot path is unchanged, skips are
counted and logged as a WARNING with COUNTS ONLY (never names / never PII).
Per-file I/O stays fail-soft: a missing or broken source contributes nothing and
the roster is built from the remaining sources.

The four consumers delegate here so there is exactly one place that decides what
a roster key is; a ratchet test fails if any consumer re-inlines a ``str(cid)``
roster merge (see ``tests/test_a6_g3_resolver_shared_validators.py``).
"""
from __future__ import annotations

import json
import logging
from typing import Dict, Optional

from .schema import canonical_numeric_cid

_log = logging.getLogger("dispatch.identity.roster")


def load_courier_names(
    kurier_ids_path,
    courier_names_path,
    grafik_full_names_path=None,
    *,
    log: Optional[logging.Logger] = None,
) -> Dict[str, str]:
    """Build the canonical ``{cid: name}`` roster from up to three sources.

    Args:
        kurier_ids_path: ``kurier_ids.json`` = ``{name: cid}`` (inverse), lowest
            priority, first-wins per canonical CID.
        courier_names_path: ``courier_names.json`` = ``{cid: name}``, higher
            priority (manually curated, wins over the inverse fallback).
        grafik_full_names_path: ``grafik_full_names.json`` = ``{full_name: cid}``,
            highest priority (panel-authoritative full names). ``None`` disables
            this source entirely — the ranking / SLA / telegram consumers never
            read grafik, so they pass ``None`` and keep their exact baseline set.
        log: logger for the fail-soft / skip WARNINGs (defaults to this module's
            logger); consumers pass their own so warnings keep their identity.

    Every CID is validated by :func:`canonical_numeric_cid`; a non-canonical
    record is skipped and counted. Returns ``{canonical_cid_str: name}``.
    """
    log = log or _log
    merged: Dict[str, str] = {}
    skipped = {"kurier_ids": 0, "courier_names": 0, "grafik_full_names": 0}

    # kurier_ids.json = {name: cid} — inverse fallback, lowest priority.
    try:
        with open(kurier_ids_path) as f:
            ids = json.load(f)
        for name, cid in ids.items():
            canon = canonical_numeric_cid(cid)
            if canon is None:
                skipped["kurier_ids"] += 1
                continue
            if canon not in merged:
                merged[canon] = name
    except Exception as e:  # fail-soft: bad/missing source -> roster from the rest
        log.warning(f"load_courier_names: kurier_ids fallback fail: {e}")

    # courier_names.json = {cid: name} — higher priority, overrides the fallback.
    try:
        with open(courier_names_path) as f:
            names = json.load(f)
        for cid_str, name in names.items():
            canon = canonical_numeric_cid(cid_str)
            if canon is None:
                skipped["courier_names"] += 1
                continue
            merged[canon] = name
    except FileNotFoundError:
        pass
    except Exception as e:
        log.warning(f"load_courier_names: courier_names fail: {e}")

    # grafik_full_names.json = {full_name: cid} — highest priority, optional.
    if grafik_full_names_path is not None:
        try:
            with open(grafik_full_names_path, encoding="utf-8") as f:
                gfn = json.load(f)
            for full_name, cid in gfn.items():
                if not (isinstance(full_name, str) and full_name.strip()):
                    continue
                canon = canonical_numeric_cid(cid)
                if canon is None:
                    skipped["grafik_full_names"] += 1
                    continue
                merged[canon] = full_name
        except FileNotFoundError:
            pass
        except Exception as e:
            log.warning(f"load_courier_names: grafik_full_names fail: {e}")

    total_skipped = sum(skipped.values())
    if total_skipped:
        log.warning(
            "load_courier_names: skipped %d non-canonical CID record(s) "
            "(kurier_ids=%d, courier_names=%d, grafik_full_names=%d)",
            total_skipped, skipped["kurier_ids"], skipped["courier_names"],
            skipped["grafik_full_names"],
        )
    return merged
