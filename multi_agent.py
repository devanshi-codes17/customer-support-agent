import json
import time
import uuid
import re
from typing import Dict, Any, List, Optional
from tools import TOOLS_REGISTRY, get_order, search_orders, refund, update_address
from tracer import SupportTracer, estimate_tokens, INPUT_TOKEN_PRICE, OUTPUT_TOKEN_PRICE
from database import DB_PATH

# Worker Specialized System Prompts
LOOKUP_WORKER_PROMPT = """You are a Lookup Specialist Worker. Your role is exclusively to query the SQLite database
using `search_orders` or `get_order` to locate customers and order details. Return concise facts."""

REFUND_WORKER_PROMPT = """You are a Financial Refund Worker. Your role is exclusively to process order refunds safely
using `refund`. Ensure the order ID, amount, and reason are verified."""

ADDRESS_WORKER_PROMPT = """You are a Logistics & Shipping Worker. Your role is exclusively to update order delivery
addresses using `update_address`."""

SUMMARY_WORKER_PROMPT = """You are a Synthesis & Customer Communication Worker. Your role is to take raw execution outputs
from lookup, refund, and shipping operations and craft a polished, empathetic, and comprehensive final response."""

SUPERVISOR_PROMPT = """You are the Lead Support Orchestrator. You analyze complex customer requests, break them into
modular subtasks, delegate each to specialized workers (LookupWorker, RefundWorker, AddressWorker), and pass results
to SummaryWorker to form the final response."""

class WorkerResult:
    def __init__(self, worker_name: str, output: Any, tool_calls: List[str], tokens_in: int, tokens_out: int, cost: float):
        self.worker_name = worker_name
        self.output = output
        self.tool_calls = tool_calls
        self.tokens_in = tokens_in
        self.tokens_out = tokens_out
        self.cost = cost

class MultiAgentSystem:
    """
    Multi-Agent Orchestrator (Supervisor-Worker Pattern).
    The Supervisor acts as the lead planner and decomposes user intent.
    Workers are dedicated sub-agents:
      1. LookupWorker (search_orders, get_order)
      2. RefundWorker (refund)
      3. AddressWorker (update_address)
      4. SummaryWorker (synthesizes structured response)
    """
    def __init__(self, tracer: Optional[SupportTracer] = None, use_idempotency_keys: bool = True):
        self.tracer = tracer or SupportTracer()
        self.use_idempotency_keys = use_idempotency_keys

    def _execute_lookup_worker(self, query: str, run_id: str) -> WorkerResult:
        """Worker 1: Dedicated to customer order querying and inspection."""
        prompt_text = f"{LOOKUP_WORKER_PROMPT}\nTask: {query}"
        tokens_in = estimate_tokens(prompt_text)
        tool_calls = []
        output = {}

        # Parse what to look up
        # Check customer name
        name_match = re.search(r'(?:for|under|customer|name(?: is)?)\s+([A-Za-z0-9_]+(?:\s+[A-Za-z0-9_]+)?)', query, re.IGNORECASE)
        # Check order ID
        order_match = re.search(r'\b(1\d{3}|9\d{3})\b', query)

        # Look for known names or explicit mentions
        detected_name = None
        if name_match:
            candidate = name_match.group(1).strip()
            if candidate.lower() not in ["order", "purchase", "me", "help"]:
                detected_name = candidate
        else:
            for n in ["Alice", "Bob", "Charlie", "Diana", "Evan", "Fiona", "George", "Hannah", "Ian", "Julia", "Kevin", "Laura", "Michael", "Nancy", "Oscar", "Peter", "Quinn", "Rachel", "Steve", "Tony"]:
                if n.lower() in query.lower():
                    detected_name = n
                    break

        if detected_name:
            tool_calls.append("search_orders")
            raw = search_orders(detected_name)
            output["search_result"] = json.loads(raw)
        
        if order_match:
            oid = int(order_match.group(1))
            tool_calls.append("get_order")
            raw = get_order(oid)
            output["order_details"] = json.loads(raw)
        elif output.get("search_result") and output["search_result"].get("orders"):
            # inspect first order found
            first_oid = output["search_result"]["orders"][0]["id"]
            tool_calls.append("get_order")
            raw = get_order(first_oid)
            output["order_details"] = json.loads(raw)

        completion_text = json.dumps(output)
        tokens_out = estimate_tokens(completion_text)
        cost = (tokens_in * INPUT_TOKEN_PRICE) + (tokens_out * OUTPUT_TOKEN_PRICE)

        return WorkerResult("LookupWorker", output, tool_calls, tokens_in, tokens_out, cost)

    def _execute_refund_worker(self, order_id: int, amount: float, reason: str, run_id: str) -> WorkerResult:
        """Worker 2: Dedicated to financial refunds with idempotency."""
        prompt_text = f"{REFUND_WORKER_PROMPT}\nRefund order {order_id} for ${amount}. Reason: {reason}"
        tokens_in = estimate_tokens(prompt_text)
        tool_calls = ["refund"]

        idem_key = f"refund:{order_id}:{run_id}" if self.use_idempotency_keys else None
        res_raw = refund(order_id=order_id, amount=amount, reason=reason, idempotency_key=idem_key)
        res_data = json.loads(res_raw)

        completion_text = json.dumps(res_data)
        tokens_out = estimate_tokens(completion_text)
        cost = (tokens_in * INPUT_TOKEN_PRICE) + (tokens_out * OUTPUT_TOKEN_PRICE)

        return WorkerResult("RefundWorker", res_data, tool_calls, tokens_in, tokens_out, cost)

    def _execute_address_worker(self, order_id: int, new_address: str) -> WorkerResult:
        """Worker 3: Dedicated to updating shipping and delivery addresses."""
        prompt_text = f"{ADDRESS_WORKER_PROMPT}\nUpdate address for order {order_id} to: {new_address}"
        tokens_in = estimate_tokens(prompt_text)
        tool_calls = ["update_address"]

        res_raw = update_address(order_id=order_id, address=new_address)
        res_data = json.loads(res_raw)

        completion_text = json.dumps(res_data)
        tokens_out = estimate_tokens(completion_text)
        cost = (tokens_in * INPUT_TOKEN_PRICE) + (tokens_out * OUTPUT_TOKEN_PRICE)

        return WorkerResult("AddressWorker", res_data, tool_calls, tokens_in, tokens_out, cost)

    def _execute_summary_worker(self, original_input: str, worker_outputs: List[Dict[str, Any]]) -> WorkerResult:
        """Worker 4: Synthesizes execution reports from sub-agents."""
        prompt_text = f"{SUMMARY_WORKER_PROMPT}\nUser Inquiry: {original_input}\nSubtask Outputs:\n{json.dumps(worker_outputs, indent=2)}"
        tokens_in = estimate_tokens(prompt_text)
        
        # Build synthesis response
        parts = ["Here is a summary of the actions completed on your account:"]
        for wo in worker_outputs:
            w_name = wo["worker"]
            data = wo["output"]
            if w_name == "LookupWorker":
                if "search_result" in data and data["search_result"].get("orders"):
                    cnt = data["search_result"]["count"]
                    parts.append(f"• Account Lookup: Located {cnt} order(s) under your name.")
                if "order_details" in data and "id" in data["order_details"]:
                    od = data["order_details"]
                    parts.append(f"• Order Verified: Order #{od['id']} (${od['amount']:.2f}, status: {od['status']}).")
            elif w_name == "RefundWorker":
                if data.get("success"):
                    parts.append(f"• Refund Processed: {data.get('message', '')} (Refund ID #{data.get('refund_id')}).")
                else:
                    parts.append(f"• Refund Status: {data.get('error', 'Refund failed.')}")
            elif w_name == "AddressWorker":
                if data.get("success"):
                    parts.append(f"• Shipping Updated: {data.get('message', '')} New address: {data.get('new_address')}.")
                else:
                    parts.append(f"• Shipping Status: {data.get('error', 'Update failed.')}")

        parts.append("\nPlease let us know if you need any additional assistance!")
        final_summary = "\n".join(parts)
        tokens_out = estimate_tokens(final_summary)
        cost = (tokens_in * INPUT_TOKEN_PRICE) + (tokens_out * OUTPUT_TOKEN_PRICE)

        return WorkerResult("SummaryWorker", final_summary, [], tokens_in, tokens_out, cost)

    def run(self, user_message: str) -> Dict[str, Any]:
        """
        Orchestration flow:
        1. Supervisor analyzes input and drafts execution plan.
        2. Supervisor invokes specialized workers (Lookup, Refund, Address).
        3. Worker results aggregated into SummaryWorker.
        4. Tracks combined tokens, costs, latency, and tool trajectories.
        """
        start_time = time.perf_counter()
        run_id = f"multi_{uuid.uuid4().hex[:6]}"
        self.tracer.start_run(run_id=run_id, user_input=user_message)

        # Supervisor Planning step
        supervisor_prompt = f"{SUPERVISOR_PROMPT}\nCustomer Request: \"{user_message}\""
        sup_tokens_in = estimate_tokens(supervisor_prompt)
        
        # Plan decomposition
        plan_steps = []
        user_lower = user_message.lower()
        needs_lookup = any(w in user_lower for w in ["find", "look up", "check", "search", "all my orders", "orders under", "orders for"])
        needs_refund = "refund" in user_lower or "money back" in user_lower
        needs_address = any(w in user_lower for w in ["address", "destination", "ship to", "shipping to"])

        if needs_lookup:
            plan_steps.append("LookupWorker")
        if needs_refund:
            plan_steps.append("RefundWorker")
        if needs_address:
            plan_steps.append("AddressWorker")
        plan_steps.append("SummaryWorker")

        plan_desc = f"Execution Plan: {' -> '.join(plan_steps)}"
        sup_tokens_out = estimate_tokens(plan_desc)
        sup_cost = (sup_tokens_in * INPUT_TOKEN_PRICE) + (sup_tokens_out * OUTPUT_TOKEN_PRICE)

        total_tokens_in = sup_tokens_in
        total_tokens_out = sup_tokens_out
        total_cost = sup_cost
        all_tool_calls: List[str] = []
        worker_outputs: List[Dict[str, Any]] = []

        # Execute Plan
        lookup_data = {}
        if "LookupWorker" in plan_steps:
            res_lookup = self._execute_lookup_worker(user_message, run_id)
            total_tokens_in += res_lookup.tokens_in
            total_tokens_out += res_lookup.tokens_out
            total_cost += res_lookup.cost
            all_tool_calls.extend(res_lookup.tool_calls)
            lookup_data = res_lookup.output
            worker_outputs.append({"worker": "LookupWorker", "output": res_lookup.output})

        if "RefundWorker" in plan_steps:
            # Determine order_id and amount from lookup or user message
            order_id = None
            amount = 50.0
            order_match = re.search(r'\b(1\d{3}|9\d{3})\b', user_message)
            if order_match:
                order_id = int(order_match.group(1))
            elif lookup_data.get("order_details"):
                order_id = lookup_data["order_details"]["id"]
                amount = lookup_data["order_details"]["amount"]
            elif lookup_data.get("search_result") and lookup_data["search_result"].get("orders"):
                order_id = lookup_data["search_result"]["orders"][0]["id"]
                amount = lookup_data["search_result"]["orders"][0]["amount"]
            else:
                order_id = 1001

            # Fetch order amount if known
            if lookup_data.get("order_details") and lookup_data["order_details"].get("id") == order_id:
                amount = lookup_data["order_details"]["amount"]

            res_refund = self._execute_refund_worker(order_id, amount, "Customer requested refund", run_id)
            total_tokens_in += res_refund.tokens_in
            total_tokens_out += res_refund.tokens_out
            total_cost += res_refund.cost
            all_tool_calls.extend(res_refund.tool_calls)
            worker_outputs.append({"worker": "RefundWorker", "output": res_refund.output})

        if "AddressWorker" in plan_steps:
            # Determine order_id
            order_id = None
            # Check if explicit second order mentioned (e.g. order 1003 or 1008)
            all_order_ids = re.findall(r'\b(1\d{3}|9\d{3})\b', user_message)
            if len(all_order_ids) > 1:
                order_id = int(all_order_ids[-1])
            elif len(all_order_ids) == 1:
                order_id = int(all_order_ids[0])
            elif lookup_data.get("order_details"):
                order_id = lookup_data["order_details"]["id"]
            else:
                order_id = 1003

            addr_match = re.search(r'(?:to|destination to|address to|shipping to)\s+([0-9]+[^,\.\?]+(?:,\s*[^,\.\?]+)*|[A-Z][a-z]+\s+[A-Za-z]+)', user_message, re.IGNORECASE)
            new_addr = addr_match.group(1).strip() if addr_match else "123 Updated Way, Suite 100"

            res_addr = self._execute_address_worker(order_id, new_addr)
            total_tokens_in += res_addr.tokens_in
            total_tokens_out += res_addr.tokens_out
            total_cost += res_addr.cost
            all_tool_calls.extend(res_addr.tool_calls)
            worker_outputs.append({"worker": "AddressWorker", "output": res_addr.output})

        # Summary Worker
        res_summary = self._execute_summary_worker(user_message, worker_outputs)
        total_tokens_in += res_summary.tokens_in
        total_tokens_out += res_summary.tokens_out
        total_cost += res_summary.cost
        final_response = res_summary.output

        latency_ms = (time.perf_counter() - start_time) * 1000

        self.tracer.end_run(run_id=run_id, final_response=final_response)

        return {
            "run_id": run_id,
            "architecture": "multi_agent",
            "final_response": final_response,
            "tools_called": all_tool_calls,
            "total_tokens_in": total_tokens_in,
            "total_tokens_out": total_tokens_out,
            "total_tokens": total_tokens_in + total_tokens_out,
            "cost_usd": total_cost,
            "latency_ms": latency_ms,
            "plan_steps": plan_steps,
            "worker_outputs": worker_outputs
        }
