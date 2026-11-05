import json
import os
import sys
import sqlite3
from agent import CustomerSupportAgent
from tracer import SupportTracer
from database import init_db, DB_PATH

def run_experiment(mode: str = "after", verbose: bool = False):
    """
    mode: 'before' (guardrails off, fail triggers loop)
          'after'  (guardrails on: step limit, token budget, repeat loop detection, human escalation)
    """
    tasks_file = os.path.join(os.path.dirname(__file__), "tasks.jsonl")
    with open(tasks_file, "r", encoding="utf-8") as f:
        tasks = [json.loads(line) for line in f if line.strip()]

    # Reset DB
    init_db(reset=True)

    trace_file = os.path.join(os.path.dirname(__file__), f"traces_{mode}.jsonl")
    if os.path.exists(trace_file):
        os.remove(trace_file)

    tracer = SupportTracer(trace_file=trace_file, verbose=verbose)

    if mode == "before":
        print("\n" + "=" * 65)
        print("⚡ RUNNING EXPERIMENT: [BEFORE] - UNGUARDED AGENT (FAIL/LOOP MODE)")
        print("   Guardrails: DISABLED | Fail Mode: ACTIVE")
        print("=" * 65)
        agent = CustomerSupportAgent(
            tracer=tracer,
            enable_guardrails=False,
            fail_mode=True,
            max_steps=25
        )
    else:
        print("\n" + "=" * 65)
        print("🛡️ RUNNING EXPERIMENT: [AFTER] - GUARDED AGENT (BUDGET + LOOP + ESCALATION)")
        print("   Guardrails: ENABLED | Cap: 10 steps | Budget: $0.0015 | Repeat: 2")
        print("=" * 65)
        agent = CustomerSupportAgent(
            tracer=tracer,
            enable_guardrails=True,
            fail_mode=True,
            max_steps=10,
            cost_budget_usd=0.0015,
            repeat_threshold=2
        )

    results = []
    total_steps = 0
    total_cost = 0.0
    stuck_count = 0
    escalated_count = 0

    for i, t in enumerate(tasks, 1):
        user_input = t["input"]
        res = agent.run(user_input)

        steps = res.get("steps", 1)
        cost = res.get("cost_usd", 0.0)
        escalated = res.get("escalated", False)
        reason = res.get("escalation_reason")

        # In "before" mode, an agent is stuck if it ran > 10 steps or repeated calls endlessly
        is_stuck = escalated or steps >= 10

        total_steps += steps
        total_cost += cost
        if is_stuck:
            stuck_count += 1
        if escalated:
            escalated_count += 1

        results.append({
            "task_id": i,
            "input": user_input,
            "steps": steps,
            "cost_usd": cost,
            "is_stuck": is_stuck,
            "escalated": escalated,
            "reason": reason
        })

        tag = "🔴 STUCK" if is_stuck else "🟢 OK"
        esc_info = f"[Escalated: {reason}]" if escalated else ""
        print(f"Task {i:02d}/50: {tag} | Steps: {steps:2d} | Cost: ${cost:.6f} {esc_info}")

    avg_steps = total_steps / len(tasks)
    avg_cost = total_cost / len(tasks)
    stuck_rate = (stuck_count / len(tasks)) * 100

    print("\n" + "=" * 65)
    print(f"📊 SUMMARY REPORT FOR [{mode.upper()}] RUN:")
    print(f"   • Total Tasks:             {len(tasks)}")
    print(f"   • Stuck Tasks:             {stuck_count}/{len(tasks)} ({stuck_rate:.1f}%)")
    print(f"   • Escalated to Human:      {escalated_count}/{len(tasks)}")
    print(f"   • Average Steps per Task:  {avg_steps:.2f}")
    print(f"   • Average Cost per Task:   ${avg_cost:.6f}")
    print(f"   • Total Experiment Cost:   ${total_cost:.6f}")
    print(f"   • Traces saved to:         {trace_file}")

    # Check tickets in DB
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("SELECT COUNT(*) FROM tickets")
    tickets_count = cursor.fetchone()[0]
    conn.close()
    print(f"   • Human Tickets in DB:     {tickets_count}")
    print("=" * 65 + "\n")

    return {
        "mode": mode,
        "stuck_rate": stuck_rate,
        "stuck_count": stuck_count,
        "escalated_count": escalated_count,
        "avg_steps": avg_steps,
        "avg_cost": avg_cost,
        "total_cost": total_cost,
        "tickets_count": tickets_count
    }

if __name__ == "__main__":
    mode_arg = sys.argv[1] if len(sys.argv) > 1 else "after"
    verbose_arg = "--verbose" in sys.argv
    run_experiment(mode=mode_arg, verbose=verbose_arg)

