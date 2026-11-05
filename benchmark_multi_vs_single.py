import json
import os
import sys
import time
from typing import List, Dict, Any
from database import init_db
from agent import CustomerSupportAgent
from multi_agent import MultiAgentSystem
from tracer import SupportTracer

def score_success(actual_tools: List[str], expected_tools: List[str], final_response: str) -> bool:
    """
    Evaluates task success based on:
    1. Tool trajectory match: Did it execute the required tools for the request?
    2. Response completeness: Does the answer contain meaningful content?
    """
    # Multiset check: did it call all expected tools?
    has_all_expected = all(t in actual_tools for t in expected_tools)
    has_content = len(final_response.strip()) > 20
    return has_all_expected and has_content

def run_comparison_benchmark(tasks_file: str = "hard_tasks.jsonl", verbose: bool = False):
    """
    Runs the Single-Agent baseline vs. Multi-Agent architecture head-to-head
    on the exact same set of 25 complex tasks.
    Logs success, total tokens, cost, latency, and tool calls.
    """
    if not os.path.exists(tasks_file):
        tasks_file = os.path.join(os.path.dirname(__file__), "hard_tasks.jsonl")

    with open(tasks_file, "r", encoding="utf-8") as f:
        tasks = [json.loads(line) for line in f if line.strip()]

    print("\n" + "=" * 80)
    print(f"🚀 RUNNING PROJECT 3 HEAD-TO-HEAD BENCHMARK: SINGLE-AGENT VS MULTI-AGENT")
    print(f"   Evaluation Dataset: {len(tasks)} Complex & Multi-Step Support Tasks")
    print("=" * 80 + "\n")

    # -------------------------------------------------------------
    # 1. RUN SINGLE-AGENT BASELINE
    # -------------------------------------------------------------
    print(">>> [Phase 1/2] Running Single-Agent Baseline...")
    init_db(reset=True)
    single_tracer = SupportTracer(trace_file="traces_single_hard.jsonl", verbose=verbose)
    single_agent = CustomerSupportAgent(
        tracer=single_tracer,
        enable_guardrails=True,
        cost_budget_usd=0.015,
        max_steps=12,
        use_idempotency_keys=True
    )

    single_results = []
    for i, t in enumerate(tasks, 1):
        u_in = t["input"]
        exp_tools = t["expected_tools"]

        t0 = time.perf_counter()
        res = single_agent.run(u_in)
        latency = (time.perf_counter() - t0) * 1000

        actual_tools = res["tools_called"]
        resp = res["final_response"]
        success = score_success(actual_tools, exp_tools, resp)

        tokens_in = res.get("tokens_in", 0)
        tokens_out = res.get("tokens_out", 0)
        cost = res.get("cost_usd", 0.0)

        # Estimate tokens if not directly available
        for run in single_tracer.runs:
            if run["run_id"] == res["run_id"]:
                tokens_in = run["total_input_tokens"]
                tokens_out = run["total_output_tokens"]
                cost = run["total_cost_usd"]
                break

        single_results.append({
            "id": t["id"],
            "input": u_in,
            "category": t.get("category", ""),
            "expected_tools": exp_tools,
            "actual_tools": actual_tools,
            "success": success,
            "total_tokens": tokens_in + tokens_out,
            "tokens_in": tokens_in,
            "tokens_out": tokens_out,
            "cost_usd": cost,
            "latency_ms": latency
        })

    # -------------------------------------------------------------
    # 2. RUN MULTI-AGENT SYSTEM
    # -------------------------------------------------------------
    print(">>> [Phase 2/2] Running Multi-Agent Orchestrator (Supervisor + 4 Workers)...")
    init_db(reset=True)
    multi_tracer = SupportTracer(trace_file="traces_multi_hard.jsonl", verbose=verbose)
    multi_system = MultiAgentSystem(tracer=multi_tracer, use_idempotency_keys=True)

    multi_results = []
    for i, t in enumerate(tasks, 1):
        u_in = t["input"]
        exp_tools = t["expected_tools"]

        res = multi_system.run(u_in)
        actual_tools = res["tools_called"]
        resp = res["final_response"]
        success = score_success(actual_tools, exp_tools, resp)

        multi_results.append({
            "id": t["id"],
            "input": u_in,
            "category": t.get("category", ""),
            "expected_tools": exp_tools,
            "actual_tools": actual_tools,
            "success": success,
            "total_tokens": res["total_tokens"],
            "tokens_in": res["total_tokens_in"],
            "tokens_out": res["total_tokens_out"],
            "cost_usd": res["cost_usd"],
            "latency_ms": res["latency_ms"]
        })

    # -------------------------------------------------------------
    # 3. COMPILATION & COMPARISON
    # -------------------------------------------------------------
    s_success_count = sum(1 for r in single_results if r["success"])
    m_success_count = sum(1 for r in multi_results if r["success"])

    s_avg_tokens = sum(r["total_tokens"] for r in single_results) / len(single_results)
    m_avg_tokens = sum(r["total_tokens"] for r in multi_results) / len(multi_results)

    s_avg_cost = sum(r["cost_usd"] for r in single_results) / len(single_results)
    m_avg_cost = sum(r["cost_usd"] for r in multi_results) / len(multi_results)

    s_avg_lat = sum(r["latency_ms"] for r in single_results) / len(single_results)
    m_avg_lat = sum(r["latency_ms"] for r in multi_results) / len(multi_results)

    s_total_tools = sum(len(r["actual_tools"]) for r in single_results)
    m_total_tools = sum(len(r["actual_tools"]) for r in multi_results)

    token_mult = m_avg_tokens / max(1, s_avg_tokens)
    cost_mult = m_avg_cost / max(0.000001, s_avg_cost)

    print("\n" + "=" * 80)
    print("📊 FINAL COMPARATIVE BENCHMARK TABLE (25 COMPLEX TASKS)")
    print("=" * 80)
    print(f"{'Metric':<32} | {'Single-Agent':<18} | {'Multi-Agent':<18} | {'Delta / Multiplier':<20}")
    print("-" * 95)
    print(f"{'Task Success Rate':<32} | {s_success_count}/{len(tasks)} ({(s_success_count/len(tasks)*100):.1f}%)    | {m_success_count}/{len(tasks)} ({(m_success_count/len(tasks)*100):.1f}%)     | {'+' if m_success_count >= s_success_count else ''}{((m_success_count - s_success_count)/len(tasks)*100):.1f}%")
    print(f"{'Avg Tokens / Task':<32} | {s_avg_tokens:<18.1f} | {m_avg_tokens:<18.1f} | {token_mult:.1f}x tokens")
    print(f"{'Avg Cost / Task':<32} | ${s_avg_cost:<17.6f} | ${m_avg_cost:<17.6f} | {cost_mult:.1f}x cost")
    print(f"{'Avg Latency / Task (ms)':<32} | {s_avg_lat:<18.2f} | {m_avg_lat:<18.2f} | {m_avg_lat / max(0.01, s_avg_lat):.1f}x latency")
    print(f"{'Total Tool Calls Executed':<32} | {s_total_tools:<18} | {m_total_tools:<18} | +{m_total_tools - s_total_tools} calls")
    print("=" * 80 + "\n")

    # Category breakdown analysis
    print("🔍 CATEGORY PERFORMANCE BREAKDOWN:")
    categories = set(r["category"] for r in single_results)
    for cat in sorted(categories):
        s_cat_success = sum(1 for r in single_results if r["category"] == cat and r["success"])
        m_cat_success = sum(1 for r in multi_results if r["category"] == cat and r["success"])
        cat_count = sum(1 for r in single_results if r["category"] == cat)
        print(f"   • {cat:<28}: Single-Agent {s_cat_success}/{cat_count} vs Multi-Agent {m_cat_success}/{cat_count}")

    return {
        "single": {
            "success_rate": (s_success_count / len(tasks)) * 100,
            "avg_tokens": s_avg_tokens,
            "avg_cost": s_avg_cost,
            "avg_latency": s_avg_lat,
            "results": single_results
        },
        "multi": {
            "success_rate": (m_success_count / len(tasks)) * 100,
            "avg_tokens": m_avg_tokens,
            "avg_cost": m_avg_cost,
            "avg_latency": m_avg_lat,
            "results": multi_results
        }
    }

if __name__ == "__main__":
    verbose = "--verbose" in sys.argv
    run_comparison_benchmark(verbose=verbose)

