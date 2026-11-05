# Project 1: Loop Detection + Token Budget Customer Support Agent

An intelligent, self-monitoring Customer Support Agent that detects infinite loops, enforces per-task token and cost budgets, caps recursion steps, and gracefully hands over stuck conversations to human operators via automated SQLite ticketing.

---

## 🎯 The Problem
Without guardrails, agents frequently get stuck when tools return unhelpful or empty outputs (e.g., misspelled names or sync delays). An unmonitored agent will call the same tool repeatedly (10, 20, 50+ times), burning through API token budgets and keeping users waiting forever before crashing.

---

## 🛡️ The Solution: Multi-Layer Guardrails & Escalation

1. **Step Limit Cap (`max_steps=10`)**:
   Caps execution iterations. When reached, instead of crashing with a stack overflow or recursion error, it outputs a polite handover response.
2. **Token & Cost Budget Enforcement (`cost_budget_usd=$0.0015`)**:
   Counts prompt and completion tokens on every turn and tracks cumulative cost using standard LLM pricing ($0.15/1M input, $0.60/1M output). If a task exceeds its budget threshold (e.g. 3× normal task cost), it immediately halts.
3. **Repeat Tool Call / Loop Detection (`repeat_threshold=2`)**:
   Maintains a hashable history of `(tool_name, arguments)`. If the exact same tool and argument signature is requested more than twice, the agent classifies the behavior as an infinite loop and stops execution.
4. **Human Escalation Ticketing (`tickets` table in SQLite)**:
   Whenever a guardrail is tripped (budget, loop, or step limit), the agent creates an actionable ticket in SQLite containing:
   - `ticket_id` (e.g. `TICK-D517C0`)
   - `user_input`
   - `reason` (e.g. `"Repetitive tool call detected (get_order({"order_id": 9999}) called 3 times)"`)
   - `steps_count` & `cost_usd`
   - `conversation_history` & `trace_id`
   and communicates the ticket ID directly to the user.

---

## 📊 Before vs. After Benchmark (50 Tasks Evaluation)

We evaluated 50 customer tasks under an adversarial retry / fail mode condition:

| Metric | Before (Unguarded Agent) | After (Guarded Agent) | Improvement |
| :--- | :--- | :--- | :--- |
| **Max Steps Allowed** | 25 iterations | 10 iterations | **60% tighter bound** |
| **Average Steps / Task** | **24.52 steps** | **3.92 steps** | **84.0% reduction** |
| **Average Cost / Task** | **$0.002607** | **$0.000254** | **90.3% cost savings** |
| **Total Experiment Cost (50 tasks)** | **$0.130360** | **$0.012711** | **Saved $0.1176 (10.2x cheaper)** |
| **Infinite Loop Containment** | None (ran to hard ceiling) | Stopped at repeat #2 | **Instant loop mitigation** |
| **Human Escalation Tickets Logged**| 0 structured tickets | **49 tickets created in DB** | **Full human-in-the-loop audit trail** |

---

## 🔍 "Before" Traces (Unguarded Loop Examples)

Below are representative excerpts from `traces_before.jsonl` demonstrating how the unguarded agent gets stuck in infinite loops:

### Example 1: Infinite Order Verification Loop (Order ID not found)
```
[TRACE START] Run ID: run_before_01
Customer Input: "Can you check order 1001?"

Step 1:
   Model: "I will retrieve details for order #1001."
   Tool Call: get_order({"order_id": 1001}) -> {"id": 1001, "customer": "Alice Smith", "status": "shipped"}

Step 2:
   Model: "Order 9999 returned not found. Retrying verification..."
   Tool Call: get_order({"order_id": 9999}) -> {"error": "Order with ID 9999 not found."}

Step 3:
   Model: "Order 9999 returned not found. Retrying verification..."
   Tool Call: get_order({"order_id": 9999}) -> {"error": "Order with ID 9999 not found."}

Step 4 through 25:
   [REPEATED 22 MORE TIMES UNTIL HARD ABORT]
   Tool Call: get_order({"order_id": 9999}) -> {"error": "Order with ID 9999 not found."}
   ...
   Cost accumulated: $0.002543 (Spent 25 steps with zero progress)
```

### Example 2: Customer Name Search Loop
```
[TRACE START] Run ID: run_before_11
Customer Input: "I placed an order under Alice Smith, can you check it?"

Step 1:
   Tool Call: search_orders({"customer": "Alice Smith"})

Step 2:
   Model: "No orders found on previous attempt. Retrying search with same query in case of sync delay..."
   Tool Call: search_orders({"customer": "non_existent_user_xyz"})

Step 3 through 25:
   Model: "No orders found on previous attempt. Retrying search with same query in case of sync delay..."
   Tool Call: search_orders({"customer": "non_existent_user_xyz"})
   [CONTINUES 23 TIMES IN A ROW - BURNED $0.003044]
```

---

## ✨ "After" Traces (Guarded Loop Detection & Human Escalation)

With guardrails enabled (`traces_after.jsonl`), identical failure conditions are intercepted immediately:

```
================================================================================
🚀 [TRACE START] Run ID: run_87ce8e
👤 Customer Input: "Can you check order 1001?"
================================================================================

🧠 [Step 1] Agent Reasoned (Cost so far: $0.000053):
   💭 "I will retrieve details for order #1001."
   🛠️ Decided to call: get_order({"order_id": 1001})

⚡ [TOOL CALL] get_order
   📥 Arguments: {"order_id": 1001}
   📤 Output:    {"id": 1001, "customer": "Alice Smith", "amount": 129.99, "status": "shipped"}
   ⏱️ Latency:   2.23ms

🧠 [Step 2] Agent Reasoned (Cost so far: $0.000114):
   💭 "Order 9999 returned not found. Retrying verification..."
   🛠️ Decided to call: get_order({"order_id": 9999})
⚡ [TOOL CALL] get_order (1st attempt)

🧠 [Step 3] Agent Reasoned (Cost so far: $0.000179):
   💭 "Order 9999 returned not found. Retrying verification..."
   🛠️ Decided to call: get_order({"order_id": 9999})
⚡ [TOOL CALL] get_order (2nd attempt)

🧠 [Step 4] Agent Reasoned (Cost so far: $0.000248):
   💭 "Order 9999 returned not found. Retrying verification..."
   🛠️ Decided to call: get_order({"order_id": 9999})

🚨 [HUMAN ESCALATION TRIGGERED] Reason: Repetitive tool call detected (get_order({"order_id": 9999}) called 3 times).
   🎫 Created Ticket: TICK-D517C0

🤖 [AGENT FINAL ANSWER]:
   I noticed I am having trouble locating the necessary details automatically. 
   I've escalated this inquiry to a human representative under ticket #TICK-D517C0. 
   Someone from our support team will inspect this and reply to you directly.

💰 Total Cost: $0.000248 (1139 in, 128 out) - SAVED 90% COST & PREVENTED 21 UNNECESSARY STEPS
🏁 [TRACE END] Run run_87ce8e (escalated)
================================================================================
```

---

## 💻 How to Run the Experiments

```powershell
cd "C:\Users\Devanshi Singh\.gemini\antigravity\scratch\customer-support-agent"

# 1. Run the "Before" benchmark (unguarded agent, observe loops & runaway costs)
python experiment.py before

# 2. Run the "After" benchmark (guarded agent with loop detection, budgets & tickets)
python experiment.py after

# 3. Inspect generated human escalation tickets in SQLite
python -c "import sqlite3; conn = sqlite3.connect('support.db'); print(conn.execute('SELECT ticket_id, reason, cost_usd FROM tickets LIMIT 5').fetchall())"
```

---

# Project 2: Trajectory Evaluation + Idempotent Refunds

## 🎯 The Problem: "The Answer Looked Right, But the Path Was Wrong"
When an external payment gateway times out or encounters network latency, agents typically retry.
Without transaction idempotency:
- The agent calls `refund(order_id=1004)`. The DB processes the refund, but a network timeout occurs before the client gets the `200 OK`.
- The agent receives a timeout error and retries: `refund(order_id=1004)`.
- The agent cheerfully reports: *"Your refund has been successfully processed! Refund ID: #2."*
- **Final Answer Accuracy looks 100% correct to traditional benchmarks**, but **the customer received a double refund (money lost)**!

---

## 🛠️ The Solution: Idempotent Payment Architecture

Following the Stripe standard for [idempotent requests](https://docs.stripe.com/api/idempotent_requests):
1. **Deterministic Unique Key**:
   Every refund intent is bound to a single idempotency key created once per user task:
   `idempotency_key = refund:{order_id}:{run_id}`
2. **Atomic SQLite Unique Constraint**:
   The `refunds` table enforces a `UNIQUE(idempotency_key)` index.
3. **Idempotent Replay**:
   If a duplicate or retried request arrives with an existing key, the server **replays the cached first result** instead of issuing a second credit:
   ```json
   {
       "success": true,
       "refund_id": 1,
       "order_id": 1004,
       "refunded_amount": 250.0,
       "status": "processed",
       "idempotent_replay": true,
       "message": "Idempotent replay: Refund for order #1004 was already processed."
   }
   ```

---

## 📊 Head-to-Head Trajectory & Accuracy Evaluation (50 Tasks)

Evaluated with network timeout simulation on all refund transactions via [`trajectory_eval.py`](file:///C:/Users/Devanshi%20Singh/.gemini/antigravity/scratch/customer-support-agent/trajectory_eval.py):

| Evaluation Metric | Without Idempotency (Vulnerable) | With Idempotency (Protected) | Impact / Delta |
| :--- | :--- | :--- | :--- |
| **Duplicate Double-Refund Orders** | **10 orders double-refunded!** | **0 orders double-refunded** | **Double refunds ELIMINATED (100% safe)** |
| **Total Rows in `refunds` Table** | 22 rows (10 duplicate payouts) | **12 rows (exact 1-to-1 refunds)** | **10 duplicate payouts prevented** |
| **Answer Accuracy** | 96.0% (48/50) | 96.0% (48/50) | Looks "good" in both (blind spot!) |
| **Trajectory (Tool Path) Accuracy** | 80.0% (40/50) | 80.0% (40/50)* | Catches the retry divergence |
| **Gap (Answer Acc vs Trajectory Acc)**| **16.0% Gap** | **16.0% Gap** | **Exposes the invisible failure window** |

*\*Note: Trajectory strict match flags tasks where retries took an unexpected branch (`[get_order, refund, refund]`), exposing the exact divergence that normal answer checks miss completely.*

---

## 🔬 Evidence: SQLite Database Audit

### Without Idempotency (Vulnerable):
```sql
sqlite> SELECT order_id, COUNT(*) as cnt, GROUP_CONCAT(id), GROUP_CONCAT(amount) 
        FROM refunds GROUP BY order_id HAVING cnt > 1;

order_id | cnt | refund_ids | amounts
---------+-----+------------+-------------------
1001     | 2   | 17, 18     | $129.99, $129.99  <-- Customer paid twice!
1002     | 2   | 3, 4       | $45.50, $45.50    <-- Customer paid twice!
1004     | 2   | 1, 2       | $250.00, $250.00  <-- Customer paid twice!
1006     | 2   | 19, 20     | $64.20, $64.20    <-- Customer paid twice!
... (10 orders compromised)
```

### With Idempotency Key (Protected):
```sql
sqlite> SELECT order_id, COUNT(*) as cnt FROM refunds GROUP BY order_id HAVING cnt > 1;
-- 0 rows returned! Zero duplicate transactions.
```

---

## 🚀 How to Run Project 2 Evaluation

```powershell
cd "C:\Users\Devanshi Singh\.gemini\antigravity\scratch\customer-support-agent"

# Run both evaluations side-by-side:
python trajectory_eval.py both

# Or run individual modes:
python trajectory_eval.py without
python trajectory_eval.py with
```

---

# Project 3: Single-Agent vs. Multi-Agent Benchmark

## 🎯 The Problem: Does Multi-Agent Actually Justify Its Overhead?
Orchestrator-worker patterns are popular in agentic design, but Anthropic reported that multi-agent research setups consume **up to 15× more tokens** than standard single-agent flows. 
Does dividing customer support into supervisor and sub-agents deliver higher quality, or does it simply inflate latency and cost?

---

## 🏗️ Architectures Tested

1. **Single-Agent Baseline ([`agent.py`](agent.py))**:
   - Single autonomous ReAct loop with access to all 4 customer support tools.
   - Enforces recursion cap (`max_steps=10`) and token/cost budgets from Project 1.
2. **Multi-Agent Orchestrator ([`multi_agent.py`](multi_agent.py))**:
   - **Lead Supervisor**: Parses customer intent, drafts subtask plans, and delegates.
   - **LookupWorker**: Dedicated customer discovery and order inspection (`search_orders`, `get_order`).
   - **RefundWorker**: Dedicated financial refund executor with idempotency (`refund`).
   - **AddressWorker**: Dedicated delivery logistics specialist (`update_address`).
   - **SummaryWorker**: Aggregates output fragments into a coherent, customer-facing response.

---

## 📊 Head-to-Head Benchmark Results (25 Complex Scenarios)

Evaluated across multi-step lookups, cross-order investigations, parallel tasks, and reconciliation queries ([`benchmark_multi_vs_single.py`](benchmark_multi_vs_single.py)):

| Evaluation Metric | Single-Agent Baseline | Multi-Agent (Supervisor + 4 Workers) | Delta / Multiplier |
| :--- | :--- | :--- | :--- |
| **Task Success Rate** | **28.0% (7/25)** | **76.0% (19/25)** | **+48.0% Success Gain** |
| **Average Tokens / Task** | 1,064.9 tokens | 733.7 tokens | **0.7× tokens (focused prompts)** |
| **Average Cost / Task** | $0.000207 | $0.000210 | **+1.4% cost delta** |
| **Average Latency / Task**| **5.06 ms** | **7.62 ms** | **1.5× latency (orchestration)** |
| **Total Tool Calls Made** | 53 calls | 73 calls | **+20 calls executed** |

---

## 🔍 Deep-Dive: Where Multi-Agent Won vs. Where It Just Cost More

```
Category                        Single-Agent    Multi-Agent     Winner
-----------------------------------------------------------------------------
lookup_refund_summary (7 tasks)    0 / 7 (0%)      7 / 7 (100%)    🏆 Multi-Agent (+100%)
lookup_address_summary (5 tasks)   0 / 5 (0%)      5 / 5 (100%)    🏆 Multi-Agent (+100%)
lookup_refund_address (2 tasks)    0 / 2 (0%)      2 / 2 (100%)    🏆 Multi-Agent (+100%)
composite_multi_action (1 task)    0 / 1 (0%)      1 / 1 (100%)    🏆 Multi-Agent (+100%)
investigation_cross_action (2)     0 / 2 (0%)      1 / 2 (50%)     🏆 Multi-Agent (+50%)
independent_parallel (8 tasks)     7 / 8 (88%)     3 / 8 (38%)     🏆 Single-Agent (+50%)
```

### 1. Where Multi-Agent Actually Won:
- **Multi-step dependencies (`lookup -> filter -> refund -> summarize`)**: Single-agent models suffer from context dilution or terminate prematurely after executing only the first action. The supervisor-worker model reliably decomposed the workflow, ensuring all required steps executed in sequence.

### 2. Where Multi-Agent Cost More Without Justification:
- **Simple independent parallel actions (e.g. "Refund order 1001 and change address on 1003")**: Single-agent handles immediate multi-tool emissions with zero coordination overhead. Multi-agent introduces supervisor routing latency, extra coordination hops, and summary pass overhead.

---

## 📜 The Decision Rule

> ### **The Antigravity Architectural Decision Rule:**
>
> 1. **Default to Single-Agent for 80% of Customer Support Tasks**:  
>    Use a single-agent baseline with strong guardrails (step limits, token budgets, and loop detectors) for standard queries, single-action requests, and straightforward parallel tool calling. It offers lower latency, zero orchestration overhead, and high execution accuracy.
>
> 2. **Promote to Multi-Agent (Supervisor-Worker) ONLY WHEN**:
>    - The task requires **3+ distinct stages of dependent reasoning** (e.g., account discovery $\rightarrow$ eligibility filtering $\rightarrow$ financial mutation $\rightarrow$ formal resolution synthesis).
>    - Subtask specialization requires isolated system prompts (e.g., strict financial verification vs. creative/empathic customer messaging).
>    - **Benchmark threshold**: Empirical task success improves by **$\ge 25\%$** over the single-agent baseline to justify the added latency and coordination hops.

---

## 🚀 How to Run the Benchmark

```powershell
cd "C:\Users\Devanshi Singh\.gemini\antigravity\scratch\customer-support-agent"

# Run the 25-task Single-Agent vs. Multi-Agent Benchmark:
python benchmark_multi_vs_single.py
```


