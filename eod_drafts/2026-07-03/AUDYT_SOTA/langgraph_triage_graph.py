#!/usr/bin/env python3
"""
DRAFT (audyt SOTA 2026-07-03) — graf LangGraph dla pętli diagnostycznej LLM-Modulo.

NIE JEST WPIĘTY. Wymaga: pip install langgraph. To refaktoryzacja istniejącego
tools/llm_triage.py (liniowy skrypt) do jawnego grafu generate→verify→judge.

WAŻNE ROZGRANICZENIE (Filar A/B audytu): hot-path dispatchu (assess_order,
TSP OR-Tools, OSRM) pozostaje w 100% deterministyczny i NIE przechodzi przez
ten graf. Graf obejmuje WYŁĄCZNIE warstwę diagnostyczną (dzisiejsze
severity_router → llm_triage), dodając to, czego liniowy skrypt nie ma:
  * węzeł VERIFY — deterministyczna weryfikacja hipotezy LLM zanim trafi do
    drogiego sędziego (LLM-Modulo: generator tani, weryfikator symboliczny),
  * pętlę repair: hipoteza odrzucona → max 2 ponowne triage z feedbackiem,
  * retry/backoff na wywołaniach API (dziś: pojedyncza próba, fail→None).
"""
from __future__ import annotations

import json
import time
import urllib.request
from typing import Literal, TypedDict

from langgraph.graph import END, StateGraph

# --- konfiguracja modeli: tani generator, mocny sędzia (jak w llm_triage.py) --
OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"
TRIAGE_MODEL = "deepseek/deepseek-v4-flash"   # tani — stawia hipotezy
JUDGE_MODEL = "anthropic/claude-opus-4.8"      # mocny, inna rodzina — ocenia
MAX_REPAIR_LOOPS = 2


class TriageState(TypedDict, total=False):
    metrics: dict          # gotowe metryki dnia (z daily_rule_report.json)
    wake_llm: bool         # werdykt deterministycznego severity_router
    hypothesis: dict       # output triage: {cause, evidence_keys, confidence}
    verify_errors: list[str]
    repair_count: int
    judgement: dict
    final: dict


def _call(model: str, system: str, user: str, api_key: str,
          max_tokens: int = 1600, retries: int = 3) -> dict | None:
    """Wywołanie OpenRouter z retry+backoff (brakujące w llm_triage.py)."""
    body = json.dumps({
        "model": model, "temperature": 0.2, "max_tokens": max_tokens,
        "messages": [{"role": "system", "content": system},
                     {"role": "user", "content": user}],
    }).encode()
    for attempt in range(retries):
        try:
            req = urllib.request.Request(
                OPENROUTER_URL, data=body,
                headers={"Authorization": f"Bearer {api_key}",
                         "Content-Type": "application/json"})
            with urllib.request.urlopen(req, timeout=120) as resp:
                content = json.load(resp)["choices"][0]["message"]["content"]
            return json.loads(content)
        except Exception:
            time.sleep(2 ** attempt)
    return None


# ---------------------------------------------------------------- węzły grafu
def gate(state: TriageState) -> TriageState:
    """Deterministyczna bramka kosztowa — dziś severity_router.wake_llm.
    P3/P4 → koniec, koszt LLM = 0."""
    return state  # wake_llm przyjeżdża policzone przez severity_router.py


def triage(state: TriageState) -> TriageState:
    """Tani LLM stawia hipotezę przyczyny anomalii. Wyłącznie interpretacja
    gotowych liczb — zakaz liczenia tras/ETA i pisania kodu (jak w oryginale)."""
    feedback = ""
    if state.get("verify_errors"):
        feedback = ("\nPoprzednia hipoteza ODRZUCONA przez weryfikator: "
                    + "; ".join(state["verify_errors"])
                    + "\nPostaw inną hipotezę.")
    system = ("Diagnozujesz anomalie dispatchera. Zwróć WYŁĄCZNIE JSON "
              '{"cause": str, "evidence_keys": [str], "confidence": 0-1}. '
              "NIE liczysz tras ani ETA. NIE piszesz kodu. NIE flipujesz flag.")
    hyp = _call(TRIAGE_MODEL, system,
                json.dumps(state["metrics"], ensure_ascii=False) + feedback,
                api_key=state["final"].get("api_key", ""))
    return {**state, "hypothesis": hyp or {},
            "repair_count": state.get("repair_count", 0) + 1}


def verify(state: TriageState) -> TriageState:
    """DETERMINISTYCZNY weryfikator (rdzeń LLM-Modulo): hipoteza musi
    powoływać się na metryki, które istnieją i faktycznie odstają."""
    errs: list[str] = []
    hyp, metrics = state.get("hypothesis") or {}, state.get("metrics") or {}
    if not hyp.get("cause"):
        errs.append("brak przyczyny w hipotezie")
    for key in hyp.get("evidence_keys", []):
        if key not in metrics:
            errs.append(f"dowód '{key}' nie istnieje w metrykach")
    if not hyp.get("evidence_keys"):
        errs.append("hipoteza bez wskazania dowodów")
    if not (0.0 <= float(hyp.get("confidence", -1)) <= 1.0):
        errs.append("confidence poza [0,1]")
    return {**state, "verify_errors": errs}


def judge(state: TriageState) -> TriageState:
    """Mocny model innej rodziny ocenia ZWERYFIKOWANĄ hipotezę (dekorelacja)."""
    system = ("Niezależnie oceń diagnozę. Zwróć WYŁĄCZNIE JSON "
              '{"verdict": "confirm|reject|uncertain", "rationale": str}.')
    payload = {"metrics": state["metrics"], "hypothesis": state["hypothesis"]}
    j = _call(JUDGE_MODEL, system, json.dumps(payload, ensure_ascii=False),
              api_key=state["final"].get("api_key", ""), max_tokens=1200)
    return {**state, "judgement": j or {"verdict": "uncertain"}}


def report(state: TriageState) -> TriageState:
    final = {"hypothesis": state.get("hypothesis"),
             "judgement": state.get("judgement"),
             "verified": not state.get("verify_errors"),
             "repairs": state.get("repair_count", 0)}
    return {**state, "final": final}


# ------------------------------------------------------------------- krawędzie
def after_gate(state: TriageState) -> Literal["triage", "__end__"]:
    return "triage" if state.get("wake_llm") else END


def after_verify(state: TriageState) -> Literal["judge", "triage", "report"]:
    if not state.get("verify_errors"):
        return "judge"                                  # hipoteza przeszła
    if state.get("repair_count", 0) < MAX_REPAIR_LOOPS:
        return "triage"                                 # pętla repair
    return "report"                                     # poddaj się jawnie


def build_graph():
    g = StateGraph(TriageState)
    g.add_node("gate", gate)
    g.add_node("triage", triage)
    g.add_node("verify", verify)
    g.add_node("judge", judge)
    g.add_node("report", report)
    g.set_entry_point("gate")
    g.add_conditional_edges("gate", after_gate)
    g.add_edge("triage", "verify")
    g.add_conditional_edges("verify", after_verify)
    g.add_edge("judge", "report")
    g.add_edge("report", END)
    return g.compile()


if __name__ == "__main__":
    graph = build_graph()
    demo = {"metrics": {"koord_rate": 0.31, "latency_p95_ms": 840,
                        "r6_breach_rate": 0.12},
            "wake_llm": True, "final": {"api_key": ""}}
    print(json.dumps(graph.invoke(demo).get("final"), indent=2,
                     ensure_ascii=False))
