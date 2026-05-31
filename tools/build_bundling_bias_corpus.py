#!/usr/bin/env python3
"""
Seed-korpus bundling-bias z SAME_REST_RACE_PROBE visible-but-filtered captures.

Kontekst: race probe Baanko (2026-05-29..31) rozstrzygnął fork — orphan=0,
visible-but-filtered dominuje → sibling z tej samej restauracji BYŁ w bagu kuriera
(kurier w puli), ale Ziomek wybrał innego jako best. Pytanie Kroku 2 (Lekcja #154):
czy BIAS toward in-bag-same-restaurant-courier trafiłby w ground-truth człowieka?

Metodologia (Lekcja #154 — flip-direction toward/away vs ground-truth, NIE intuicja):
  Dla każdej visible-but-filtered capture:
    - bias_target = sib.cid  (kurier już niosący zlecenie tej restauracji)
    - shadow_best = best_cid z probe (kogo Ziomek-shadow wybrał)
    - prod_proposed = proposed z PANEL_OVERRIDE (produkcja) lub = final gdy brak override
    - final = ASSIGNED <oid> -> <cid>  (ground-truth: co realnie się stało)
  Klasyfikacja interwencji „przesuń propozycję na sib_cid":
    TOWARD  : final == bias_target            → bias trafiłby w ground-truth (ZA)
    AWAY    : final == prod_proposed != sib   → bias odsunąłby od ground-truth (PRZECIW)
    OTHER   : final == trzeci kurier          → człowiek chciał kogoś innego (bias i tak pudło)
    UNKNOWN : brak ASSIGNED (KOORD / niewzięte / anulowane)

Reprodukowalne: czyta tylko logi (shadow.log + dispatch.log), zero side-effects.
"""
import json
import re
import sys
from collections import OrderedDict, Counter

SHADOW = "/root/.openclaw/workspace/scripts/logs/shadow.log"
DISPATCH = "/root/.openclaw/workspace/scripts/logs/dispatch.log"
TS_FLOOR = "2026-05-29 13:33:08"  # post-restart probe; wyklucza test-leak 13:32:16

PROBE_RE = re.compile(
    r"SAME_REST_RACE_PROBE oid=(\S+) rest='([^']*)' best_cid=(\S*) "
    r"orphan=(\w+) visible_not_proposed=(\w+) sibs=(\[.*\])"
)
ASSIGNED_RE = re.compile(r"panel_watcher: ASSIGNED (\d+) -> (\d+)")
OVERRIDE_RE = re.compile(
    r"PANEL_OVERRIDE oid=(\d+) proposed=(\d+) \(score=([^)]+)\) actual=(\d+)"
)
KOORD_RE = re.compile(r"oid=(\d+).*KOORD", re.IGNORECASE)


def load_visible_captures():
    """Zwraca OrderedDict oid -> capture-dict dla visible-but-filtered (in-bag sibling)."""
    out = OrderedDict()
    with open(SHADOW) as f:
        for line in f:
            if "SAME_REST_RACE_PROBE" not in line:
                continue
            ts = line[:19]
            if ts < TS_FLOOR:
                continue
            m = PROBE_RE.search(line)
            if not m:
                continue
            oid, rest, best, orphan, vis, sibs_raw = m.groups()
            if vis != "True":
                continue  # tylko visible-but-filtered
            try:
                sibs = json.loads(sibs_raw)
            except json.JSONDecodeError:
                continue
            # in-bag sibling = ten z którym byśmy bundlowali
            inbag = [s for s in sibs if s.get("sibling_in_courier_bag") and s.get("cid")]
            if not inbag:
                continue
            sib = inbag[0]
            if oid in out:
                continue  # pierwsza visible capture per oid
            out[oid] = dict(
                ts=ts, oid=oid, rest=rest,
                shadow_best_cid=best or None,
                sib_oid=sib.get("oid"),
                sib_cid=str(sib.get("cid")),
                sib_status=sib.get("status"),
                sib_pos_source=sib.get("courier_pos_source"),
                sib_bag_size=sib.get("courier_bag_size"),
                sib_assigned_age_s=sib.get("assigned_age_s"),
            )
    return out


def load_outcomes(oids):
    """Dla zbioru oid: final assigned + override (proposed/actual) z dispatch.log."""
    assigned = {}   # oid -> cid (ostatni ASSIGNED wygrywa)
    override = {}   # oid -> (proposed, score, actual)
    koord = set()
    want = set(oids)
    with open(DISPATCH) as f:
        for line in f:
            if "ASSIGNED" in line:
                m = ASSIGNED_RE.search(line)
                if m and m.group(1) in want:
                    assigned[m.group(1)] = m.group(2)
            if "PANEL_OVERRIDE" in line:
                m = OVERRIDE_RE.search(line)
                if m and m.group(1) in want:
                    override[m.group(1)] = (m.group(2), m.group(3), m.group(4))
            if "KOORD" in line:
                m = KOORD_RE.search(line)
                if m and m.group(1) in want:
                    koord.add(m.group(1))
    return assigned, override, koord


def classify(cap, assigned, override, koord):
    oid = cap["oid"]
    sib = cap["sib_cid"]
    final = assigned.get(oid)
    if oid in override:
        proposed, score, actual = override[oid]
        cap["prod_proposed_cid"] = proposed
        cap["prod_proposed_score"] = score
        cap["override"] = True
        cap["override_actual_cid"] = actual
        # final powinien == actual; preferuj ASSIGNED jeśli jest
        final = final or actual
    else:
        # brak override → produkcja proposed == final assigned
        cap["prod_proposed_cid"] = final
        cap["prod_proposed_score"] = None
        cap["override"] = False
        cap["override_actual_cid"] = None
    cap["final_cid"] = final
    # czy sib był w ogóle proposed przez produkcję?
    cap["sib_was_prod_proposed"] = (cap.get("prod_proposed_cid") == sib)

    if final is None:
        cap["flip"] = "UNKNOWN_KOORD" if oid in koord else "UNKNOWN_NOASSIGN"
    elif final == sib:
        cap["flip"] = "TOWARD"
    elif final == cap["prod_proposed_cid"]:
        cap["flip"] = "AWAY"
    else:
        cap["flip"] = "OTHER"
    return cap


def main():
    caps = load_visible_captures()
    assigned, override, koord = load_outcomes(caps.keys())
    rows = [classify(c, assigned, override, koord) for c in caps.values()]

    # agregaty
    flip = Counter(r["flip"] for r in rows)
    ps = Counter(r["sib_pos_source"] for r in rows)
    overrides = sum(1 for r in rows if r["override"])
    toward_via_override = sum(
        1 for r in rows if r["flip"] == "TOWARD" and r["override"]
    )

    summary = dict(
        n=len(rows),
        flip=dict(flip),
        n_override=overrides,
        toward_total=flip.get("TOWARD", 0),
        toward_via_override=toward_via_override,
        away_total=flip.get("AWAY", 0),
        other_total=flip.get("OTHER", 0),
        unknown=flip.get("UNKNOWN_KOORD", 0) + flip.get("UNKNOWN_NOASSIGN", 0),
        pos_source_dist=dict(ps),
    )

    print(json.dumps(dict(summary=summary, rows=rows), indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
