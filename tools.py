import sqlite3
import json
import os

DB_PATH = os.path.join(os.path.dirname(__file__), "support.db")

def get_db_connection():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def get_order(order_id: int) -> str:
    """Fetch an order by its numerical ID.

    Args:
        order_id: The ID of the order to look up.

    Returns:
        JSON string containing the order details or an error message if not found.
    """
    try:
        order_id = int(order_id)
    except (ValueError, TypeError):
        return json.dumps({"error": f"Invalid order ID format: {order_id}"})

    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT id, customer, amount, status, address FROM orders WHERE id = ?", (order_id,))
    row = cursor.fetchone()
    conn.close()

    if row:
        return json.dumps({
            "id": row["id"],
            "customer": row["customer"],
            "amount": row["amount"],
            "status": row["status"],
            "address": row["address"]
        })
    return json.dumps({"error": f"Order with ID {order_id} not found."})

def search_orders(customer: str) -> str:
    """Search for orders belonging to a customer by name (case-insensitive substring match).

    Args:
        customer: Customer name or partial name.

    Returns:
        JSON string with list of matching orders.
    """
    if not customer or not customer.strip():
        return json.dumps({"error": "Customer name query cannot be empty."})

    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute(
        "SELECT id, customer, amount, status, address FROM orders WHERE LOWER(customer) LIKE ?",
        (f"%{customer.strip().lower()}%",)
    )
    rows = cursor.fetchall()
    conn.close()

    results = [
        {
            "id": r["id"],
            "customer": r["customer"],
            "amount": r["amount"],
            "status": r["status"],
            "address": r["address"]
        }
        for r in rows
    ]
    return json.dumps({"count": len(results), "orders": results})

# Global simulation controls for testing
SIMULATE_REFUND_TIMEOUT = False
_TIMEOUT_STATE = {}

def set_refund_timeout_simulation(enabled: bool):
    global SIMULATE_REFUND_TIMEOUT, _TIMEOUT_STATE
    SIMULATE_REFUND_TIMEOUT = enabled
    _TIMEOUT_STATE = {}

def refund(order_id: int, amount: float, reason: str = "Customer requested refund", idempotency_key: str = None) -> str:
    """Issue a refund for a given order ID and amount.
    Supports idempotency key (e.g. refund:{order_id}:{request_id}) to prevent double-charging/double-refunding.
    
    Args:
        order_id: The numerical ID of the order to refund.
        amount: The refund amount requested.
        reason: Optional explanation for the refund.
        idempotency_key: Optional unique identifier for the refund request.

    Returns:
        JSON string confirming refund status or error.
    """
    global SIMULATE_REFUND_TIMEOUT, _TIMEOUT_STATE

    try:
        order_id = int(order_id)
        amount = float(amount)
    except (ValueError, TypeError):
        return json.dumps({"error": "Invalid order_id or amount format."})

    if amount <= 0:
        return json.dumps({"error": "Refund amount must be greater than 0."})

    conn = get_db_connection()
    cursor = conn.cursor()

    # 1. IDEMPOTENCY CHECK
    if idempotency_key:
        cursor.execute(
            "SELECT r.id, r.order_id, r.amount, o.status FROM refunds r JOIN orders o ON r.order_id = o.id WHERE r.idempotency_key = ?",
            (idempotency_key,)
        )
        existing = cursor.fetchone()
        if existing:
            conn.close()
            return json.dumps({
                "success": True,
                "refund_id": existing["id"],
                "order_id": existing["order_id"],
                "refunded_amount": existing["amount"],
                "status": "processed",
                "idempotent_replay": True,
                "message": f"Idempotent replay: Refund for order #{existing['order_id']} was already processed."
            })

    # Check if order exists
    cursor.execute("SELECT id, amount, status FROM orders WHERE id = ?", (order_id,))
    order = cursor.fetchone()
    if not order:
        conn.close()
        return json.dumps({"error": f"Cannot refund. Order {order_id} does not exist."})

    # Check existing refunds to prevent over-refunding (only enforced if idempotency_key is not used or distinct)
    cursor.execute("SELECT COALESCE(SUM(amount), 0) as total_refunded FROM refunds WHERE order_id = ?", (order_id,))
    total_refunded = cursor.fetchone()["total_refunded"]
    remaining_eligible = order["amount"] - total_refunded

    # If without idempotency keys, duplicate charges can over-refund or double record partial refunds
    if amount > remaining_eligible and idempotency_key is not None:
        conn.close()
        return json.dumps({
            "error": f"Refund amount ${amount:.2f} exceeds remaining eligible balance ${remaining_eligible:.2f} (Order total: ${order['amount']:.2f}, already refunded: ${total_refunded:.2f})."
        })

    # Record the refund
    cursor.execute(
        "INSERT INTO refunds (order_id, amount, reason, idempotency_key) VALUES (?, ?, ?, ?)",
        (order_id, amount, reason, idempotency_key)
    )
    refund_id = cursor.lastrowid
    
    # If full refund, update order status to refunded
    if remaining_eligible - amount <= 0.001:
        cursor.execute("UPDATE orders SET status = 'refunded' WHERE id = ?", (order_id,))

    conn.commit()
    conn.close()

    # 2. TIMEOUT SIMULATION (Fails first attempt from caller perspective AFTER DB commit)
    # This accurately reproduces real-world network drops: payment gateway debits/refunds,
    # but the network connection drops before the caller receives HTTP 200 OK!
    if SIMULATE_REFUND_TIMEOUT:
        attempt_count = _TIMEOUT_STATE.get(order_id, 0)
        _TIMEOUT_STATE[order_id] = attempt_count + 1
        if attempt_count == 0:
            # First attempt triggers simulated network timeout
            return json.dumps({
                "error": "GatewayTimeout: The payment gateway did not respond within 5000ms. Please retry the request."
            })

    return json.dumps({
        "success": True,
        "refund_id": refund_id,
        "order_id": order_id,
        "refunded_amount": amount,
        "status": "processed",
        "idempotent_replay": False,
        "message": f"Successfully refunded ${amount:.2f} for order #{order_id}."
    })

def update_address(order_id: int, address: str) -> str:
    """Update shipping address for an order if it hasn't been delivered or cancelled yet.

    Args:
        order_id: The numerical ID of the order.
        address: The new delivery address.

    Returns:
        JSON string confirming address update or failure reason.
    """
    try:
        order_id = int(order_id)
    except (ValueError, TypeError):
        return json.dumps({"error": "Invalid order ID format."})

    if not address or not address.strip():
        return json.dumps({"error": "New address cannot be empty."})

    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT id, status, address FROM orders WHERE id = ?", (order_id,))
    order = cursor.fetchone()

    if not order:
        conn.close()
        return json.dumps({"error": f"Order #{order_id} not found."})

    # Business rule: Can only change address for orders that are not yet delivered or cancelled
    if order["status"] in ["delivered", "cancelled"]:
        conn.close()
        return json.dumps({
            "error": f"Cannot update address: Order #{order_id} status is already '{order['status']}'."
        })

    old_address = order["address"]
    cursor.execute("UPDATE orders SET address = ? WHERE id = ?", (address.strip(), order_id))
    conn.commit()
    conn.close()

    return json.dumps({
        "success": True,
        "order_id": order_id,
        "old_address": old_address,
        "new_address": address.strip(),
        "message": f"Shipping address for order #{order_id} updated successfully."
    })

TOOLS_REGISTRY = {
    "get_order": get_order,
    "search_orders": search_orders,
    "refund": refund,
    "update_address": update_address
}

TOOL_SCHEMAS = [
    {
        "type": "function",
        "function": {
            "name": "get_order",
            "description": "Fetch order details (customer, amount, status, address) by order ID.",
            "parameters": {
                "type": "object",
                "properties": {
                    "order_id": {
                        "type": "integer",
                        "description": "The unique numerical identifier of the order."
                    }
                },
                "required": ["order_id"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "search_orders",
            "description": "Search for orders by customer name (partial or full match). Use this when the customer did not provide an order ID.",
            "parameters": {
                "type": "object",
                "properties": {
                    "customer": {
                        "type": "string",
                        "description": "Customer name or partial name to search for."
                    }
                },
                "required": ["customer"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "refund",
            "description": "Process a refund for an order. First verify the order details using get_order to check amount and status.",
            "parameters": {
                "type": "object",
                "properties": {
                    "order_id": {
                        "type": "integer",
                        "description": "The unique ID of the order to refund."
                    },
                    "amount": {
                        "type": "number",
                        "description": "The refund amount in dollars."
                    },
                    "reason": {
                        "type": "string",
                        "description": "Optional reason for the refund."
                    },
                    "idempotency_key": {
                        "type": "string",
                        "description": "Unique key to ensure idempotent processing and prevent duplicate charges or refunds."
                    }
                },
                "required": ["order_id", "amount"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "update_address",
            "description": "Update the delivery address for an order. Fails if order is already delivered or cancelled.",
            "parameters": {
                "type": "object",
                "properties": {
                    "order_id": {
                        "type": "integer",
                        "description": "The order ID to update."
                    },
                    "address": {
                        "type": "string",
                        "description": "The new shipping address."
                    }
                },
                "required": ["order_id", "address"]
            }
        }
    }
]

if __name__ == "__main__":
    print("Testing get_order(1001):", get_order(1001))
    print("Testing search_orders('Tony'):", search_orders("Tony"))
    print("Testing update_address(1003, '999 New Street, Austin, TX'):", update_address(1003, "999 New Street, Austin, TX"))
    print("Testing refund(1003, 50.0):", refund(1003, 50.0))

