import json
import time
from datetime import datetime
from typing import List, Dict, Any, Optional

# Standard Pricing reference (e.g. GPT-4o-mini / Gemini Flash class):
# Input: $0.15 per 1M tokens ($0.00000015 per token)
# Output: $0.60 per 1M tokens ($0.00000060 per token)
INPUT_TOKEN_PRICE = 0.15 / 1_000_000
OUTPUT_TOKEN_PRICE = 0.60 / 1_000_000

def estimate_tokens(text: str) -> int:
    """Rough heuristic: ~4 characters per token if tokenizer is absent."""
    if not text:
        return 0
    return max(1, len(text) // 4)

class SupportTracer:
    """
    Comprehensive tracer for Customer Support Agent executions.
    Tracks tool calls, token usage, cost accumulation, loop detection,
    and escalation status.
    """
    def __init__(self, trace_file: str = "traces.jsonl", verbose: bool = True):
        self.trace_file = trace_file
        self.verbose = verbose
        self.runs: List[Dict[str, Any]] = []

    def start_run(self, run_id: str, user_input: str) -> Dict[str, Any]:
        run_record = {
            "run_id": run_id,
            "timestamp": datetime.now().isoformat(),
            "user_input": user_input,
            "steps": [],
            "tool_calls": [],
            "total_input_tokens": 0,
            "total_output_tokens": 0,
            "total_cost_usd": 0.0,
            "escalated": False,
            "escalation_reason": None,
            "ticket_id": None,
            "final_response": None,
            "status": "in_progress"
        }
        self.runs.append(run_record)
        if self.verbose:
            print("\n" + "=" * 80)
            print(f"🚀 [TRACE START] Run ID: {run_id}")
            print(f"👤 Customer Input: \"{user_input}\"")
            print("=" * 80)
        return run_record

    def log_llm_step(
        self,
        run_id: str,
        step_index: int,
        model_thoughts: Optional[str],
        tool_requests: List[Dict[str, Any]],
        input_tokens: int = 0,
        output_tokens: int = 0
    ):
        cost = (input_tokens * INPUT_TOKEN_PRICE) + (output_tokens * OUTPUT_TOKEN_PRICE)
        for run in self.runs:
            if run["run_id"] == run_id:
                run["total_input_tokens"] += input_tokens
                run["total_output_tokens"] += output_tokens
                run["total_cost_usd"] += cost

                step_data = {
                    "type": "llm_decision",
                    "step": step_index,
                    "thoughts": model_thoughts,
                    "tool_requests": tool_requests,
                    "tokens": {
                        "input": input_tokens,
                        "output": output_tokens,
                        "step_cost_usd": cost,
                        "cumulative_cost_usd": run["total_cost_usd"]
                    },
                    "timestamp": datetime.now().isoformat()
                }
                run["steps"].append(step_data)
                if self.verbose:
                    print(f"\n🧠 [Step {step_index}] Agent Reasoned (Cost so far: ${run['total_cost_usd']:.6f}):")
                    if model_thoughts:
                        print(f"   💭 \"{model_thoughts.strip()}\"")
                    if tool_requests:
                        tools_str = ", ".join([f"{t['name']}({json.dumps(t['arguments'])})" for t in tool_requests])
                        print(f"   🛠️ Decided to call: {tools_str}")
                    else:
                        print("   💬 Decided to reply directly to customer.")
                break

    def log_tool_execution(
        self,
        run_id: str,
        tool_name: str,
        arguments: Dict[str, Any],
        output: Any,
        duration_ms: float
    ):
        for run in self.runs:
            if run["run_id"] == run_id:
                call_record = {
                    "tool_name": tool_name,
                    "arguments": arguments,
                    "output": output,
                    "duration_ms": duration_ms,
                    "timestamp": datetime.now().isoformat()
                }
                run["tool_calls"].append(call_record)
                if self.verbose:
                    print(f"\n⚡ [TOOL CALL] {tool_name}")
                    print(f"   📥 Arguments: {json.dumps(arguments)}")
                    print(f"   📤 Output:    {json.dumps(output) if isinstance(output, (dict, list)) else output}")
                    print(f"   ⏱️ Latency:   {duration_ms:.2f}ms")
                break

    def log_escalation(self, run_id: str, reason: str, ticket_id: str):
        for run in self.runs:
            if run["run_id"] == run_id:
                run["escalated"] = True
                run["escalation_reason"] = reason
                run["ticket_id"] = ticket_id
                if self.verbose:
                    print(f"\n🚨 [HUMAN ESCALATION TRIGGERED] Reason: {reason}")
                    print(f"   🎫 Created Ticket: {ticket_id}")
                break

    def end_run(self, run_id: str, final_response: str, error: Optional[str] = None):
        for run in self.runs:
            if run["run_id"] == run_id:
                run["final_response"] = final_response
                run["status"] = "failed" if error else "completed"
                run["completed_at"] = datetime.now().isoformat()
                if error:
                    run["error"] = error
                
                # Append to trace file
                try:
                    with open(self.trace_file, "a", encoding="utf-8") as f:
                        f.write(json.dumps(run) + "\n")
                except Exception as e:
                    print(f"Failed to write trace: {e}")

                if self.verbose:
                    print(f"\n🤖 [AGENT FINAL ANSWER]:")
                    print(f"   {final_response}")
                    print(f"💰 Total Cost: ${run['total_cost_usd']:.6f} ({run['total_input_tokens']} in, {run['total_output_tokens']} out)")
                    print(f"🏁 [TRACE END] Run {run_id} ({run['status']})")
                    print("=" * 80 + "\n")
                return run
        return None
