import json
import os
import sys
from agent import CustomerSupportAgent
from tracer import SupportTracer
from database import init_db

def run_benchmark(max_evals: int = 50, verbose_trace: bool = False):
    tasks_file = os.path.join(os.path.dirname(__file__), "tasks.jsonl")
    if not os.path.exists(tasks_file):
        print("tasks.jsonl not found. Run generate_tasks.py first.")
        return

    # Reset DB to clean initial state for reproducible testing
    init_db(reset=True)

    # Initialize tracer
    trace_path = os.path.join(os.path.dirname(__file__), "traces.jsonl")
    if os.path.exists(trace_path):
        os.remove(trace_path)

    tracer = SupportTracer(trace_file=trace_path, verbose=verbose_trace)
    agent = CustomerSupportAgent(tracer=tracer)

    with open(tasks_file, "r", encoding="utf-8") as f:
        tasks = [json.loads(line) for line in f if line.strip()][:max_evals]

    print(f"\n=======================================================")
    print(f"  RUNNING BENCHMARK ON {len(tasks)} CUSTOMER SUPPORT TASKS")
    print(f"=======================================================\n")

    correct = 0
    results_summary = []

    for i, t in enumerate(tasks, 1):
        user_input = t["input"]
        expected_tools = t["expected_tools"]
        category = t.get("category", "general")

        res = agent.run(user_input)
        actual_tools = res["tools_called"]

        # Check match
        is_match = (actual_tools == expected_tools)
        if is_match:
            correct += 1
            status_icon = "✅"
        else:
            status_icon = "❌"

        results_summary.append({
            "task_id": i,
            "category": category,
            "input": user_input,
            "expected": expected_tools,
            "actual": actual_tools,
            "passed": is_match
        })

        print(f"[{i:02d}/50] {status_icon} [{category}]")
        print(f"      Input:    \"{user_input}\"")
        print(f"      Expected: {expected_tools}")
        print(f"      Actual:   {actual_tools}")
        if not is_match:
            print(f"      Diff:     Expected {expected_tools} != Actual {actual_tools}")

    accuracy = (correct / len(tasks)) * 100
    print("\n" + "=" * 55)
    print(f"BENCHMARK COMPLETED: {correct}/{len(tasks)} passed ({accuracy:.1f}% accuracy)")
    print(f"Detailed tool invocation traces saved to: {trace_path}")
    print("=" * 55 + "\n")

    return results_summary

if __name__ == "__main__":
    verbose = "--verbose" in sys.argv
    run_benchmark(verbose_trace=verbose)

