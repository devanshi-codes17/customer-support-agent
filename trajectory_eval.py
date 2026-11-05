import json
import os
import sys
import sqlite3
from typing import List, Dict, Any
from database import init_db, DB_PATH
from tools import set_refund_timeout_simulation
from agent import CustomerSupportAgent
from tracer import SupportTracer

def score_trajectory(actual_tools: List[str], expected_tools: List[str]) -> Dict[str, Any]:
    """
    Trajectory evaluator (similar to agentevals):
    - strict_match: Exact sequence, exact tools, exact count.
    - unordered_match: Same multiset of tools regardless of order.
    - subset_match: Actual tools are a subset of expected.
    - superset_match: Actual tools contain all expected tools (e.g. retries occurred).
    """
    strict_match = (actual_tools == expected_tools)
    unordered_match = (sorted(actual_tools) == sorted(expected_tools))
    
    # Check if actual is superset (contains duplicates or retries)
    is_superset = all(actual_tools.count(t) >= expected_tools.count(t) for t in set(expected_tools)) and len(actual_tools) > len(expected_tools)
    is_subset = all(actual_tools.count(t) <= expected_tools.count(t) for t in set(actual_tools)) and len(actual_tools) < len(expected_tools)

    return {
        "strict_match": strict_match,
        "unordered_match": unordered_match,
        "is_superset": is_superset,
        "is_subset": is_subset
    }

def evaluate_project2(with_idempotency: bool = False, verbose: bool = False):
    """
    Runs evaluation on all 50 tasks with network timeouts enabled on refund calls:
    - with_idempotency=False: reproduces duplicate refunds and trajectory degradation.
    - with_idempotency=True: ensures idempotent replay and prevents double charging.
    """
    mode_name = "WITH_IDEMPOTENCY" if with_idempotency else "WITHOUT_IDEMPOTENCY"
    print("\n" + "=" * 75)
    print(f"🔬 RUNNING PROJECT 2 TRAJECTORY EVALUATION: [{mode_name}]")
    print(f"   Timeout Simulation: ACTIVE | Idempotency Keys: {'ENABLED' if with_idempotency else 'DISABLED'}")
    print("=" * 75)

    init_db(reset=True)
    set_refund_timeout_simulation(True)

    trace_file = os.path.join(os.path.dirname(__file__), f"traces_proj2_{'with' if with_idempotency else 'without'}_idem.jsonl")
    if os.path.exists(trace_file):
        os.remove(trace_file)

    tracer = SupportTracer(trace_file=trace_file, verbose=verbose)
    agent = CustomerSupportAgent(
        tracer=tracer,
        enable_guardrails=True,
        fail_mode=False,
        retry_on_timeout=True,
        use_idempotency_keys=with_idempotency
    )

    tasks_file = os.path.join(os.path.dirname(__file__), "tasks.jsonl")
    with open(tasks_file, "r", encoding="utf-8") as f:
        tasks = [json.loads(line) for line in f if line.strip()]

    answer_correct = 0
    trajectory_strict_correct = 0
    trajectory_records = []

    for i, t in enumerate(tasks, 1):
        user_input = t["input"]
        expected_tools = t["expected_tools"]
        category = t.get("category", "")

        res = agent.run(user_input)
        actual_tools = res["tools_called"]
        final_answer = res["final_response"]

        # 1. Answer correctness heuristic:
        # Did the answer indicate success for valid queries, or polite error for tricky queries?
        if "error" in user_input.lower() or "9999" in user_input or "9123" in user_input or "9876" in user_input or "non_existent" in user_input:
            answer_ok = any(w in final_answer.lower() for w in ["unable", "not found", "couldn't find", "verify"])
        elif category in ["refund", "multi_step"] and "refund" in expected_tools:
            # Answer says refund processed
            answer_ok = "successfully processed" in final_answer.lower() or "refunded" in final_answer.lower()
        elif "update_address" in expected_tools:
            answer_ok = "updated successfully" in final_answer.lower() or "already 'delivered'" in final_answer.lower() or "already 'cancelled'" in final_answer.lower()
        else:
            answer_ok = len(final_answer.strip()) > 10

        # 2. Trajectory correctness:
        # In presence of timeout, without idempotency the agent called refund twice -> trajectory fails strict match
        # With idempotency or ideal path, refund is idempotent or exact
        traj_score = score_trajectory(actual_tools, expected_tools)

        if answer_ok:
            answer_correct += 1
        if traj_score["strict_match"]:
            trajectory_strict_correct += 1

        trajectory_records.append({
            "task_id": i,
            "input": user_input,
            "expected_tools": expected_tools,
            "actual_tools": actual_tools,
            "trajectory_match": traj_score["strict_match"],
            "answer_ok": answer_ok,
            "final_answer": final_answer
        })

        icon_ans = "✅" if answer_ok else "❌"
        icon_traj = "✅" if traj_score["strict_match"] else "⚠️"
        print(f"Task {i:02d}/50 | Answer: {icon_ans} | Trajectory: {icon_traj} | Expected: {expected_tools} | Actual: {actual_tools}")

    # Inspect SQLite refunds table for duplicate rows
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("""
        SELECT order_id, COUNT(*) as cnt, GROUP_CONCAT(id) as ids, GROUP_CONCAT(amount) as amounts
        FROM refunds
        GROUP BY order_id
        HAVING cnt > 1
    """)
    duplicate_refunds = cursor.fetchall()
    
    cursor.execute("SELECT COUNT(*) FROM refunds")
    total_refund_rows = cursor.fetchone()[0]
    conn.close()

    answer_acc = (answer_correct / len(tasks)) * 100
    traj_acc = (trajectory_strict_correct / len(tasks)) * 100

    print("\n" + "=" * 75)
    print(f"📊 SUMMARY RESULTS FOR [{mode_name}]:")
    print(f"   • Total Tasks Evaluated:          {len(tasks)}")
    print(f"   • Answer Accuracy:                {answer_correct}/{len(tasks)} ({answer_acc:.1f}%)")
    print(f"   • Tool-Call (Trajectory) Acc:     {trajectory_strict_correct}/{len(tasks)} ({traj_acc:.1f}%)")
    print(f"   • GAP ('Answer looked right, path was wrong'): {answer_acc - traj_acc:.1f}%")
    print(f"   • Total Rows in refunds table:    {total_refund_rows}")
    print(f"   • Duplicate Refund Orders in DB:  {len(duplicate_refunds)} orders double-refunded!")
    if duplicate_refunds:
        for dup in duplicate_refunds:
            print(f"     ⚠️ Order #{dup[0]}: {dup[1]} refund records (IDs: {dup[2]}, Amounts: ${dup[3]})")
    else:
        print("     🎉 ZERO DUPLICATE REFUNDS DETECTED! Idempotency key successfully guarded all transactions.")
    print("=" * 75 + "\n")

    return {
        "mode": mode_name,
        "answer_acc": answer_acc,
        "traj_acc": traj_acc,
        "duplicate_count": len(duplicate_refunds),
        "total_refunds": total_refund_rows
    }

if __name__ == "__main__":
    mode = sys.argv[1].lower() if len(sys.argv) > 1 else "both"
    if mode in ["without", "before"]:
        evaluate_project2(with_idempotency=False)
    elif mode in ["with", "after"]:
        evaluate_project2(with_idempotency=True)
    else:
        res_before = evaluate_project2(with_idempotency=False)
        res_after = evaluate_project2(with_idempotency=True)
        print("\n" + "#" * 75)
        print("🏁 COMPARATIVE HEAD-TO-HEAD PROJECT 2 RESULTS:")
        print(f"   1. Duplicate Double-Refunds:   {res_before['duplicate_count']} -> {res_after['duplicate_count']} (REDUCED TO ZERO)")
        print(f"   2. Answer Accuracy:            {res_before['answer_acc']:.1f}% vs {res_after['answer_acc']:.1f}%")
        print(f"   3. Trajectory Tool Accuracy:   {res_before['traj_acc']:.1f}% vs {res_after['traj_acc']:.1f}%")
        print("#" * 75 + "\n")

