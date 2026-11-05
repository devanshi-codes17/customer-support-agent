import streamlit as st
import json
import sqlite3
import os
import time
from agent import CustomerSupportAgent
from multi_agent import MultiAgentSystem
from database import init_db, DB_PATH
from tools import set_refund_timeout_simulation

st.set_page_config(
    page_title="Guardrailed AI Customer Support Platform",
    page_icon="🛡️",
    layout="wide",
    initial_sidebar_state="expanded"
)

# Initialize database if not exists
if not os.path.exists(DB_PATH):
    init_db(reset=True)

# Custom Styling for polished portfolio presentation
st.markdown("""
<style>
    .metric-card {
        background-color: #f8fafc;
        border: 1px solid #e2e8f0;
        border-radius: 8px;
        padding: 12px 16px;
        margin-bottom: 8px;
    }
    .badge-success {
        background-color: #dcfce7;
        color: #166534;
        padding: 2px 8px;
        border-radius: 4px;
        font-weight: 600;
        font-size: 12px;
    }
    .badge-warning {
        background-color: #fef9c3;
        color: #854d0e;
        padding: 2px 8px;
        border-radius: 4px;
        font-weight: 600;
        font-size: 12px;
    }
    .badge-danger {
        background-color: #fee2e2;
        color: #991b1b;
        padding: 2px 8px;
        border-radius: 4px;
        font-weight: 600;
        font-size: 12px;
    }
    .stChatInput {
        position: relative;
        bottom: 0px;
    }
</style>
""", unsafe_allow_html=True)

# ---------------------------------------------------------
# SIDEBAR: Enterprise Controls & Diagnostics
# ---------------------------------------------------------
with st.sidebar:
    st.title("🛡️ Mission Control")
    st.caption("Enterprise Guardrails & Simulation Controls")
    
    st.subheader("1. Architecture Mode")
    arch_mode = st.radio(
        "Select Agent Pattern",
        ["Single-Agent (ReAct Guarded)", "Multi-Agent (Supervisor-Worker)"],
        index=0,
        help="Single-Agent runs one guarded loop. Multi-Agent delegates to 4 specialized workers."
    )

    st.subheader("2. Guardrails Configuration")
    enable_guardrails = st.toggle("Enable Safety Guardrails", value=True, help="Enforces token budget, step limit, loop detection, and ticket handover.")
    
    col_c1, col_c2 = st.columns(2)
    with col_c1:
        step_limit = st.number_input("Max Steps", min_value=2, max_value=20, value=10)
    with col_c2:
        cost_budget = st.number_input("Budget ($)", min_value=0.0001, max_value=0.05, value=0.0015, step=0.0002, format="%.4f")

    st.subheader("3. Failure & Network Injection")
    simulate_fail_loop = st.toggle("Inject Unhelpful Search Loop", value=False, help="Forces agent to retry missing searches/orders repeatedly to demonstrate loop detection.")
    simulate_timeout = st.toggle("Inject Gateway Timeout (5000ms)", value=False, help="Simulates network drops on refund to test double-refund vulnerability vs. idempotency protection.")
    use_idempotency = st.toggle("Enforce Idempotency Keys", value=True, help="Passes unique Stripe-style transaction keys to eliminate double-refunds.")

    st.divider()
    if st.button("🔄 Reset Database to Clean State", use_container_width=True):
        init_db(reset=True)
        set_refund_timeout_simulation(False)
        st.session_state.chat_history = []
        st.session_state.last_telemetry = None
        st.success("Database and session reset!")

# Sync simulation settings
set_refund_timeout_simulation(simulate_timeout)

# Session state initialization
if "chat_history" not in st.session_state:
    st.session_state.chat_history = [
        {"role": "assistant", "content": "Hello! I am your AI Support Representative. You can ask me to track orders, update delivery destinations, or issue refunds."}
    ]
if "last_telemetry" not in st.session_state:
    st.session_state.last_telemetry = None

# ---------------------------------------------------------
# MAIN INTERFACE: Split-Screen Layout
# ---------------------------------------------------------
st.title("Autonomous Customer Support Platform")
st.markdown("**Real-world enterprise demo:** Tool-calling agents with strict cost budgets, infinite loop prevention, idempotent transaction safety, and human escalation.")

col_chat, col_telemetry = st.columns([1.1, 0.9], gap="large")

# ---------------------------------------------------------
# LEFT COLUMN: Customer Chat Widget
# ---------------------------------------------------------
with col_chat:
    st.subheader("💬 Customer Experience Portal")

    # Quick Scenario Prompts
    st.caption("Try one of these real-world scenarios:")
    btn_cols = st.columns(3)
    preset_query = None
    if btn_cols[0].button("📦 Check Order #1001", use_container_width=True):
        preset_query = "Can you check the delivery status of order 1001?"
    if btn_cols[1].button("💳 Refund Order #1004", use_container_width=True):
        preset_query = "Please process a full refund for order 1004."
    if btn_cols[2].button("🔄 Refund + Change Address", use_container_width=True):
        preset_query = "Please refund order 1003 and update its address to 555 Pine Road."

    # Render Chat History
    chat_container = st.container(height=420)
    with chat_container:
        for msg in st.session_state.chat_history:
            avatar = "👤" if msg["role"] == "user" else "🤖"
            with st.chat_message(msg["role"], avatar=avatar):
                st.markdown(msg["content"])

    # Chat Input Box
    user_input = st.chat_input("Type your message here...")
    active_prompt = user_input or preset_query

    if active_prompt:
        # Add user message
        st.session_state.chat_history.append({"role": "user", "content": active_prompt})
        
        # Instantiate appropriate agent
        start_t = time.perf_counter()
        if "Multi-Agent" in arch_mode:
            system = MultiAgentSystem(use_idempotency_keys=use_idempotency)
            result = system.run(active_prompt)
        else:
            agent = CustomerSupportAgent(
                enable_guardrails=enable_guardrails,
                max_steps=step_limit,
                cost_budget_usd=cost_budget,
                fail_mode=simulate_fail_loop,
                use_idempotency_keys=use_idempotency,
                retry_on_timeout=True
            )
            result = agent.run(active_prompt)

        latency_ms = (time.perf_counter() - start_t) * 1000
        result["latency_ms"] = latency_ms

        # Store telemetry & update response
        st.session_state.last_telemetry = result
        st.session_state.chat_history.append({"role": "assistant", "content": result["final_response"]})
        st.rerun()

# ---------------------------------------------------------
# RIGHT COLUMN: Enterprise Telemetry & Audit Dashboard
# ---------------------------------------------------------
with col_telemetry:
    st.subheader("📊 Live Enterprise Telemetry & Guardrails")

    if st.session_state.last_telemetry:
        tel = st.session_state.last_telemetry
        
        # Key Stat Cards
        m_col1, m_col2, m_col3 = st.columns(3)
        cost_val = tel.get("cost_usd", 0.0)
        tokens_val = tel.get("total_tokens", tel.get("steps", 1) * 280)
        latency_val = tel.get("latency_ms", 12.0)

        with m_col1:
            st.metric("Task Cost", f"${cost_val:.6f}", delta="Sub-cent bound" if cost_val < cost_budget else "High", delta_color="inverse")
        with m_col2:
            st.metric("Total Tokens", f"{tokens_val}", help="Input + Output tokens tracked for financial auditing")
        with m_col3:
            st.metric("Latency", f"{latency_val:.1f} ms")

        # Guardrail Status Indicators
        st.markdown("##### Security & Safety Guardrails")
        g_c1, g_c2, g_c3 = st.columns(3)
        with g_c1:
            if tel.get("escalated"):
                st.markdown('<span class="badge-danger">🚨 Escalated to Human</span>', unsafe_allow_html=True)
            else:
                st.markdown('<span class="badge-success">🛡️ Guardrails Safe</span>', unsafe_allow_html=True)
        with g_c2:
            if use_idempotency:
                st.markdown('<span class="badge-success">🔒 Idempotency Active</span>', unsafe_allow_html=True)
            else:
                st.markdown('<span class="badge-danger">⚠️ Idempotency Off</span>', unsafe_allow_html=True)
        with g_c3:
            tools_called = tel.get("tools_called", [])
            st.markdown(f'<span class="badge-warning">⚡ {len(tools_called)} Tool Invocations</span>', unsafe_allow_html=True)

        # Tabbed Inspector: Traces, Database Records & Escalation Tickets
        t_tabs = st.tabs(["⚡ Trajectory Trace", "🎫 Escalation Tickets", "🗄️ SQLite Database"])
        
        with t_tabs[0]:
            st.markdown(f"**Execution ID:** `{tel.get('run_id')}`")
            st.markdown(f"**Tools Executed in Order:** `{tools_called}`")
            if "plan_steps" in tel:
                st.info(f"**Supervisor Orchestration Plan:** {' ➔ '.join(tel['plan_steps'])}")
            if "escalation_reason" in tel and tel["escalation_reason"]:
                st.error(f"**Handover Triggered:** {tel['escalation_reason']}")
                if "ticket_id" in tel:
                    st.warning(f"**Created Support Ticket:** `{tel['ticket_id']}`")

        with t_tabs[1]:
            conn = sqlite3.connect(DB_PATH)
            conn.row_factory = sqlite3.Row
            tickets = conn.execute("SELECT ticket_id, user_input, reason, cost_usd, status FROM tickets ORDER BY rowid DESC LIMIT 5").fetchall()
            conn.close()
            if tickets:
                st.caption("Latest 5 tickets dispatched to human triage:")
                for tick in tickets:
                    st.code(f"Ticket ID: {tick['ticket_id']}\nStatus:    {tick['status']}\nCost:      ${tick['cost_usd']:.6f}\nTrigger:   {tick['reason']}\nInquiry:   {tick['user_input']}")
            else:
                st.caption("No human escalations triggered. All requests resolved autonomously.")

        with t_tabs[2]:
            conn = sqlite3.connect(DB_PATH)
            conn.row_factory = sqlite3.Row
            refunds = conn.execute("SELECT id, order_id, amount, idempotency_key, timestamp FROM refunds ORDER BY id DESC LIMIT 5").fetchall()
            orders = conn.execute("SELECT id, customer, amount, status FROM orders LIMIT 5").fetchall()
            conn.close()

            st.caption("Recent Refunds in Database:")
            if refunds:
                st.dataframe([dict(r) for r in refunds], use_container_width=True)
            else:
                st.caption("No refunds processed yet.")

            st.caption("Orders Table (Sample):")
            st.dataframe([dict(o) for o in orders], use_container_width=True)

    else:
        st.info("💡 Send a customer inquiry on the left to inspect real-time execution tokens, latency, tool calls, and financial safety guardrails.")
        st.markdown("""
        **What this platform monitors in real time:**
        1. **Token Cost Budgeting**: Caps task expense at sub-cents.
        2. **Infinite Loop Detection**: Identifies repetitive identical calls and aborts.
        3. **Idempotency Keys**: Guarantees zero duplicate payouts during network timeouts.
        4. **Autonomous Ticketing**: Hands over failed conversations to human specialists with context.
        """)
