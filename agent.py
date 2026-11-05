import os
import json
import time
import uuid
import re
import sqlite3
from typing import Dict, Any, List, Optional
from tools import TOOLS_REGISTRY, TOOL_SCHEMAS, DB_PATH
from tracer import SupportTracer, estimate_tokens, INPUT_TOKEN_PRICE, OUTPUT_TOKEN_PRICE

SYSTEM_PROMPT = """You are an empathetic, precise, and helpful Customer Support Agent for an e-commerce platform.
You have direct access to tools for querying orders, finding customer orders, issuing refunds, and updating shipping addresses.

Guidelines:
1. Always look up order details with `get_order` or find orders with `search_orders` before performing refunds or address updates.
2. If the user doesn't specify an order ID but gives their name, search for their orders using `search_orders`.
3. If an order ID is given for a refund, fetch the order first to check its amount and current status before issuing a refund with `refund`.
4. If an address update is requested, check the order status first. Note that delivered or cancelled orders cannot have their addresses updated.
5. If an order cannot be found, explain this clearly and ask the user to double check their order ID or name.
6. Be courteous, clear, and professional.
"""

def create_human_ticket(
    user_input: str,
    reason: str,
    steps_count: int,
    cost_usd: float,
    trace_id: str,
    conversation_history: List[Dict[str, Any]]
) -> str:
    """Creates a 'needs human' escalation ticket in SQLite with context and history."""
    ticket_id = f"TICK-{uuid.uuid4().hex[:6].upper()}"
    try:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO tickets (ticket_id, user_input, reason, steps_count, cost_usd, trace_id, conversation_history)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (
            ticket_id,
            user_input,
            reason,
            steps_count,
            cost_usd,
            trace_id,
            json.dumps(conversation_history)
        ))
        conn.commit()
        conn.close()
    except Exception as e:
        print(f"Failed to record ticket in database: {e}")
    return ticket_id


class SimulatingLLMCaller:
    """
    LLM caller that handles standard reasoning and intentionally exhibits looping
    behavior on unhelpful tool results (e.g. misspelled names or repetitive searches)
    when guardrails are absent.
    """
    def __init__(self, fail_mode: bool = False, retry_on_timeout: bool = False, idempotency_enabled: bool = True):
        self.fail_mode = fail_mode
        self.retry_on_timeout = retry_on_timeout
        self.idempotency_enabled = idempotency_enabled

    def generate_response(self, messages: List[Dict[str, Any]]) -> Dict[str, Any]:
        user_msg = ""
        for m in messages:
            if m["role"] == "user":
                user_msg = m["content"]
                break

        last_msg = messages[-1]

        # Count how many times tools have been called so far in this thread
        tool_history = [m for m in messages if m.get("role") == "tool"]

        # Tool return evaluation
        if last_msg["role"] == "tool":
            tool_name = last_msg["name"]
            try:
                tool_output = json.loads(last_msg["content"])
            except:
                tool_output = last_msg["content"]

            # Intentional loop trigger in fail mode or unhelpful result
            if self.fail_mode:
                # If tool returned no results or error, LLM stubbornly retries or loops
                if (isinstance(tool_output, dict) and (tool_output.get("count") == 0 or "error" in tool_output)) or len(tool_history) < 25:
                    # In unprotected fail mode, repeat search_orders with same or slightly varied query
                    # or keep polling get_order
                    if tool_name == "search_orders":
                        customer = "non_existent_user_xyz"
                        return {
                            "content": "No orders found on previous attempt. Retrying search with same query in case of sync delay...",
                            "tool_calls": [{
                                "id": f"call_{uuid.uuid4().hex[:8]}",
                                "name": "search_orders",
                                "arguments": {"customer": customer}
                            }]
                        }
                    elif tool_name == "get_order":
                        order_id = 9999
                        return {
                            "content": f"Order {order_id} returned not found. Retrying verification...",
                            "tool_calls": [{
                                "id": f"call_{uuid.uuid4().hex[:8]}",
                                "name": "get_order",
                                "arguments": {"order_id": order_id}
                            }]
                        }

            # Normal continuation logic
            user_lower = user_msg.lower()
            if "refund" in user_lower and ("address" in user_lower or "ship to" in user_lower or "move" in user_lower):
                called_tools = [m.get("name") for m in messages if m["role"] == "tool"]
                if "get_order" in called_tools and "refund" not in called_tools:
                    order_id = tool_output.get("id") or 1003
                    amount = tool_output.get("amount", 50.0)
                    return {
                        "content": f"I verified order #{order_id}. Now issuing the refund.",
                        "tool_calls": [{
                            "id": f"call_{uuid.uuid4().hex[:8]}",
                            "name": "refund",
                            "arguments": {"order_id": order_id, "amount": amount}
                        }]
                    }
                elif "refund" in called_tools and "update_address" not in called_tools:
                    new_addr = "123 New St, City, ST 12345"
                    match = re.search(r'(?:to|address to|change to)\s+([0-9]+[^,\.]+(?:,\s*[^,\.]+)*)', user_msg, re.IGNORECASE)
                    if match:
                        new_addr = match.group(1).strip()
                    order_id = [m.get("arguments", {}).get("order_id") for m in messages if m.get("name") == "refund"][0]
                    return {
                        "content": f"Refund completed. Now updating address for order #{order_id}.",
                        "tool_calls": [{
                            "id": f"call_{uuid.uuid4().hex[:8]}",
                            "name": "update_address",
                            "arguments": {"order_id": order_id, "address": new_addr}
                        }]
                    }

            if tool_name == "get_order":
                if "error" in tool_output:
                    return {
                        "content": f"I checked our database, but {tool_output['error']} Please verify your order number and let me know if you need any other assistance.",
                        "tool_calls": []
                    }
                if any(w in user_msg.lower() for w in ["refund", "money back", "cancel"]):
                    return {
                        "content": f"Order #{tool_output['id']} found with status '{tool_output['status']}' and amount ${tool_output['amount']}. Now submitting the refund.",
                        "tool_calls": [{
                            "id": f"call_{uuid.uuid4().hex[:8]}",
                            "name": "refund",
                            "arguments": {"order_id": tool_output["id"], "amount": tool_output["amount"]}
                        }]
                    }
                elif any(w in user_msg.lower() for w in ["change address", "change the address", "update address", "update delivery address", "update the shipping", "change destination", "ship order", "ship to", "move", "update shipping", "change on order", "change order", "update the address"]):
                    addr_match = re.search(r'(?:to|new address:?)\s+([^\.\?]+)', user_msg, re.IGNORECASE)
                    new_addr = addr_match.group(1).strip() if addr_match else "456 Updated St, Springfield"
                    return {
                        "content": f"Order #{tool_output['id']} located. Updating address to: {new_addr}.",
                        "tool_calls": [{
                            "id": f"call_{uuid.uuid4().hex[:8]}",
                            "name": "update_address",
                            "arguments": {"order_id": tool_output["id"], "address": new_addr}
                        }]
                    }
                else:
                    return {
                        "content": f"Here are the details for Order #{tool_output['id']}:\n- Customer: {tool_output['customer']}\n- Status: {tool_output['status']}\n- Total: ${tool_output['amount']:.2f}\n- Delivery Address: {tool_output['address']}",
                        "tool_calls": []
                    }

            elif tool_name == "search_orders":
                if tool_output.get("count", 0) == 0:
                    return {
                        "content": "I couldn't find any orders matching that customer name. Could you please double-check the name or provide an order number?",
                        "tool_calls": []
                    }
                orders = tool_output.get("orders", [])
                orders_summary = "\n".join([f"- Order #{o['id']}: Status: {o['status']}, Total: ${o['amount']}, Address: {o['address']}" for o in orders])
                return {
                    "content": f"I found {len(orders)} order(s) under your name:\n{orders_summary}\n\nPlease let me know if you'd like to make any updates or issue a refund on one of these.",
                    "tool_calls": []
                }

            elif tool_name == "refund":
                if "error" in tool_output:
                    # In timeout retry mode, simulate agent retrying the refund call
                    if "GatewayTimeout" in str(tool_output) and self.retry_on_timeout:
                        # Extract order_id & amount from prior refund call
                        prior_call = [m for m in messages if m.get("role") == "tool" and m.get("name") == "refund"][-1]
                        # Find the corresponding call arguments
                        for m in reversed(messages):
                            if m.get("role") == "assistant" and m.get("tool_calls"):
                                for tc in m["tool_calls"]:
                                    if tc["function"]["name"] == "refund":
                                        args = json.loads(tc["function"]["arguments"])
                                        return {
                                            "content": "The refund request encountered a network timeout. Retrying refund request...",
                                            "tool_calls": [{
                                                "id": f"call_{uuid.uuid4().hex[:8]}",
                                                "name": "refund",
                                                "arguments": args
                                            }]
                                        }
                    return {
                        "content": f"Unable to process refund: {tool_output['error']}",
                        "tool_calls": []
                    }
                return {
                    "content": f"Your refund has been successfully processed! {tool_output.get('message', '')} Refund ID: #{tool_output.get('refund_id')}.",
                    "tool_calls": []
                }

            elif tool_name == "update_address":
                if "error" in tool_output:
                    return {
                        "content": f"Unable to update shipping address: {tool_output['error']}",
                        "tool_calls": []
                    }
                return {
                    "content": f"Your shipping address has been successfully updated! {tool_output.get('message', '')} New address: {tool_output.get('new_address')}.",
                    "tool_calls": []
                }

        # Initial customer message routing
        u_text = user_msg.lower()
        order_match = re.search(r'\b(1\d{3}|9\d{3})\b', user_msg)
        if order_match:
            order_id = int(order_match.group(1))
            return {
                "content": f"I will retrieve details for order #{order_id}.",
                "tool_calls": [{
                    "id": f"call_{uuid.uuid4().hex[:8]}",
                    "name": "get_order",
                    "arguments": {"order_id": order_id}
                }]
            }

        name_match = re.search(r'(?:for|under|customer|name(?: is)?)\s+([A-Za-z0-9_]+(?:\s+[A-Za-z0-9_]+)?)', user_msg)
        if name_match:
            customer_name = name_match.group(1)
            if customer_name.lower() not in ["order", "purchase", "me", "help", "information", "the", "details"]:
                return {
                    "content": f"I'll search for orders placed under the name {customer_name}.",
                    "tool_calls": [{
                        "id": f"call_{uuid.uuid4().hex[:8]}",
                        "name": "search_orders",
                        "arguments": {"customer": customer_name}
                    }]
                }

        for name in ["Alice", "Bob", "Charlie", "Diana", "Evan", "Fiona", "George", "Hannah", "Ian", "Julia", "Kevin", "Laura", "Michael", "Nancy", "Oscar", "Peter", "Quinn", "Rachel", "Steve", "Tony"]:
            if name.lower() in u_text:
                return {
                    "content": f"Searching orders for customer {name}.",
                    "tool_calls": [{
                        "id": f"call_{uuid.uuid4().hex[:8]}",
                        "name": "search_orders",
                        "arguments": {"customer": name}
                    }]
                }

        return {
            "content": "Hello! I am your Customer Support assistant. I can help you check order status, find orders by customer name, issue refunds, or change delivery addresses. Could you please provide your order ID or customer name?",
            "tool_calls": []
        }


class CustomerSupportAgent:
    """
    Production-ready Customer Support Agent equipped with:
    - Step limit cap (max_steps / recursion_limit)
    - Cost / Token budget enforcement
    - Repeat tool invocation / loop detection
    - Human escalation ticketing
    - Idempotency key injection for state-modifying actions (e.g. refunds)
    """
    def __init__(
        self,
        api_key: Optional[str] = None,
        model: str = "mock",
        tracer: Optional[SupportTracer] = None,
        max_steps: int = 10,
        cost_budget_usd: float = 0.0015,   # e.g., ~3x typical task cost (~$0.0004 -> $0.0012-0.0015)
        repeat_threshold: int = 2,         # Stop if identical (tool, args) called 2 times
        enable_guardrails: bool = True,    # Set to False to observe uncontrolled "before" looping
        fail_mode: bool = False,           # Injects unhelpful retry loop behavior
        retry_on_timeout: bool = True,     # Retries on network timeout errors
        use_idempotency_keys: bool = True  # Injects stable idempotency keys to prevent double-refunds
    ):
        self.api_key = api_key or os.getenv("OPENAI_API_KEY") or os.getenv("GEMINI_API_KEY")
        self.model = model
        self.tracer = tracer or SupportTracer()
        self.max_steps = max_steps
        self.cost_budget_usd = cost_budget_usd
        self.repeat_threshold = repeat_threshold
        self.enable_guardrails = enable_guardrails
        self.use_idempotency_keys = use_idempotency_keys
        self.mock_llm = SimulatingLLMCaller(
            fail_mode=fail_mode,
            retry_on_timeout=retry_on_timeout,
            idempotency_enabled=use_idempotency_keys
        )

    def _call_llm(self, messages: List[Dict[str, Any]]) -> Dict[str, Any]:
        """Calls real LLM API if key present, else simulated engine."""
        # Calculate prompt token usage
        prompt_text = "".join([str(m.get("content", "")) for m in messages])
        in_tokens = estimate_tokens(prompt_text)

        if self.api_key and self.model not in ["mock", "test"]:
            try:
                import openai
                client = openai.OpenAI(api_key=self.api_key)
                response = client.chat.completions.create(
                    model=self.model if self.model != "mock" else "gpt-4o-mini",
                    messages=messages,
                    tools=TOOL_SCHEMAS,
                    tool_choice="auto"
                )
                choice = response.choices[0].message
                tool_calls = []
                if choice.tool_calls:
                    for tc in choice.tool_calls:
                        tool_calls.append({
                            "id": tc.id,
                            "name": tc.function.name,
                            "arguments": json.loads(tc.function.arguments)
                        })
                out_tokens = response.usage.completion_tokens if hasattr(response, 'usage') and response.usage else estimate_tokens(choice.content or "")
                in_tokens = response.usage.prompt_tokens if hasattr(response, 'usage') and response.usage else in_tokens
                return {
                    "content": choice.content or "",
                    "tool_calls": tool_calls,
                    "input_tokens": in_tokens,
                    "output_tokens": out_tokens
                }
            except Exception as e:
                print(f"[Warning] Real LLM API call failed ({e}), falling back to simulated engine.")

        # Fallback simulated engine
        res = self.mock_llm.generate_response(messages)
        out_tokens = estimate_tokens(res.get("content", "") + str(res.get("tool_calls", "")))
        res["input_tokens"] = in_tokens
        res["output_tokens"] = out_tokens
        return res

    def run(self, user_message: str) -> Dict[str, Any]:
        """
        Executes the agent loop with budget, loop detection, and human escalation.
        """
        run_id = f"run_{uuid.uuid4().hex[:6]}"
        self.tracer.start_run(run_id=run_id, user_input=user_message)

        messages = [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_message}
        ]

        step = 0
        executed_tools = []
        call_signatures_history: List[str] = []
        cumulative_cost = 0.0

        loop_limit = self.max_steps if self.enable_guardrails else 25

        while step < loop_limit:
            step += 1
            llm_res = self._call_llm(messages)
            content = llm_res.get("content", "")
            tool_calls = llm_res.get("tool_calls", [])
            in_tokens = llm_res.get("input_tokens", 0)
            out_tokens = llm_res.get("output_tokens", 0)

            step_cost = (in_tokens * INPUT_TOKEN_PRICE) + (out_tokens * OUTPUT_TOKEN_PRICE)
            cumulative_cost += step_cost

            self.tracer.log_llm_step(
                run_id=run_id,
                step_index=step,
                model_thoughts=content if tool_calls else None,
                tool_requests=tool_calls,
                input_tokens=in_tokens,
                output_tokens=out_tokens
            )

            # ------------------------------------------------------------------
            # GUARDRAIL 1: COST BUDGET CHECK
            # ------------------------------------------------------------------
            if self.enable_guardrails and cumulative_cost >= self.cost_budget_usd:
                reason = f"Cost budget limit reached: task spent ${cumulative_cost:.6f} (budget: ${self.cost_budget_usd:.6f})."
                ticket_id = create_human_ticket(
                    user_input=user_message,
                    reason=reason,
                    steps_count=step,
                    cost_usd=cumulative_cost,
                    trace_id=run_id,
                    conversation_history=messages
                )
                self.tracer.log_escalation(run_id=run_id, reason=reason, ticket_id=ticket_id)
                fallback_msg = (
                    f"I apologize for the delay. To make sure your request is handled accurately without further delay, "
                    f"I have opened priority support ticket #{ticket_id} for a human specialist to review. "
                    f"A customer support agent will follow up with you shortly."
                )
                self.tracer.end_run(run_id=run_id, final_response=fallback_msg, error="Cost budget exceeded")
                return {
                    "run_id": run_id,
                    "final_response": fallback_msg,
                    "tools_called": executed_tools,
                    "escalated": True,
                    "escalation_reason": "cost_budget_exceeded",
                    "ticket_id": ticket_id,
                    "steps": step,
                    "cost_usd": cumulative_cost
                }

            # If no tools called, agent has reached final conclusion
            if not tool_calls:
                final_answer = content
                self.tracer.end_run(run_id=run_id, final_response=final_answer)
                return {
                    "run_id": run_id,
                    "final_response": final_answer,
                    "tools_called": executed_tools,
                    "escalated": False,
                    "steps": step,
                    "cost_usd": cumulative_cost
                }

            # ------------------------------------------------------------------
            # GUARDRAIL 2: REPEAT TOOL CALL / LOOP DETECTION
            # ------------------------------------------------------------------
            if self.enable_guardrails:
                loop_detected = False
                repeated_sig = ""
                for tc in tool_calls:
                    sig = f"{tc['name']}:{json.dumps(tc['arguments'], sort_keys=True)}"
                    # Count occurrences of exact call
                    prior_count = call_signatures_history.count(sig)
                    if prior_count >= self.repeat_threshold:
                        loop_detected = True
                        repeated_sig = f"{tc['name']}({json.dumps(tc['arguments'])})"
                        break

                if loop_detected:
                    reason = f"Repetitive tool call detected ({repeated_sig} called {self.repeat_threshold + 1} times)."
                    ticket_id = create_human_ticket(
                        user_input=user_message,
                        reason=reason,
                        steps_count=step,
                        cost_usd=cumulative_cost,
                        trace_id=run_id,
                        conversation_history=messages
                    )
                    self.tracer.log_escalation(run_id=run_id, reason=reason, ticket_id=ticket_id)
                    fallback_msg = (
                        f"I noticed I am having trouble locating the necessary details automatically. "
                        f"I've escalated this inquiry to a human representative under ticket #{ticket_id}. "
                        f"Someone from our support team will inspect this and reply to you directly."
                    )
                    self.tracer.end_run(run_id=run_id, final_response=fallback_msg, error="Loop detected")
                    return {
                        "run_id": run_id,
                        "final_response": fallback_msg,
                        "tools_called": executed_tools,
                        "escalated": True,
                        "escalation_reason": "loop_detected",
                        "ticket_id": ticket_id,
                        "steps": step,
                        "cost_usd": cumulative_cost
                    }

            # Append assistant message with tool calls to context
            messages.append({
                "role": "assistant",
                "content": content,
                "tool_calls": [
                    {
                        "id": tc["id"],
                        "type": "function",
                        "function": {
                            "name": tc["name"],
                            "arguments": json.dumps(tc["arguments"])
                        }
                    }
                    for tc in tool_calls
                ]
            })

            # Execute tool calls
            for tc in tool_calls:
                t_name = tc["name"]
                t_args = dict(tc["arguments"])

                # Inject stable idempotency key for refund requests if enabled
                if self.use_idempotency_keys and t_name == "refund":
                    order_id = t_args.get("order_id")
                    # Stable idempotency key tied to run_id + order_id
                    t_args["idempotency_key"] = f"refund:{order_id}:{run_id}"

                executed_tools.append(t_name)
                sig = f"{t_name}:{json.dumps(t_args, sort_keys=True)}"
                call_signatures_history.append(sig)

                start_t = time.perf_counter()
                if t_name in TOOLS_REGISTRY:
                    try:
                        raw_result = TOOLS_REGISTRY[t_name](**t_args)
                    except Exception as err:
                        raw_result = json.dumps({"error": str(err)})
                else:
                    raw_result = json.dumps({"error": f"Tool '{t_name}' not recognized."})

                duration_ms = (time.perf_counter() - start_t) * 1000

                try:
                    parsed_result = json.loads(raw_result)
                except:
                    parsed_result = raw_result

                self.tracer.log_tool_execution(
                    run_id=run_id,
                    tool_name=t_name,
                    arguments=t_args,
                    output=parsed_result,
                    duration_ms=duration_ms
                )

                messages.append({
                    "role": "tool",
                    "tool_call_id": tc["id"],
                    "name": t_name,
                    "content": raw_result
                })

        # ----------------------------------------------------------------------
        # GUARDRAIL 3: STEP LIMIT (RECURSION LIMIT) EXCEEDED
        # ----------------------------------------------------------------------
        reason = f"Step limit of {self.max_steps} iterations exceeded without resolution."
        ticket_id = create_human_ticket(
            user_input=user_message,
            reason=reason,
            steps_count=step,
            cost_usd=cumulative_cost,
            trace_id=run_id,
            conversation_history=messages
        )
        self.tracer.log_escalation(run_id=run_id, reason=reason, ticket_id=ticket_id)
        fallback_msg = (
            f"Thank you for your patience. This request requires extra attention from our support staff. "
            f"I have created ticket #{ticket_id} and transferred your case to a human specialist."
        )
        self.tracer.end_run(run_id=run_id, final_response=fallback_msg, error="Step limit exceeded")
        return {
            "run_id": run_id,
            "final_response": fallback_msg,
            "tools_called": executed_tools,
            "escalated": True,
            "escalation_reason": "step_limit_exceeded",
            "ticket_id": ticket_id,
            "steps": step,
            "cost_usd": cumulative_cost
        }
