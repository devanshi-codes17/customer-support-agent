import sqlite3
import os

DB_PATH = os.path.join(os.path.dirname(__file__), "support.db")

SAMPLE_ORDERS = [
    (1001, "Alice Smith", 129.99, "shipped", "123 Maple St, Springfield, IL 62701"),
    (1002, "Bob Jones", 45.50, "delivered", "456 Oak Ave, Seattle, WA 98101"),
    (1003, "Charlie Brown", 89.00, "processing", "789 Pine Rd, Austin, TX 78701"),
    (1004, "Diana Prince", 250.00, "delivered", "101 Elm Blvd, Metropolis, NY 10001"),
    (1005, "Evan Wright", 15.75, "cancelled", "202 Birch Way, Denver, CO 80201"),
    (1006, "Fiona Gallagher", 64.20, "shipped", "303 Cedar Dr, Chicago, IL 60601"),
    (1007, "George Clark", 199.95, "delivered", "404 Walnut Ln, Miami, FL 33101"),
    (1008, "Hannah Abbott", 32.40, "processing", "505 Chestnut St, Boston, MA 02101"),
    (1009, "Ian Malcolm", 520.00, "shipped", "606 Redwood Ct, San Diego, CA 92101"),
    (1010, "Julia Roberts", 78.50, "delivered", "707 Willow Way, Atlanta, GA 30301"),
    (1011, "Kevin Hart", 110.00, "processing", "808 Spruce Ter, Philadelphia, PA 19101"),
    (1012, "Laura Croft", 340.25, "delivered", "909 Ash Pl, Las Vegas, NV 89101"),
    (1013, "Michael Scott", 12.99, "shipped", "1725 Slough Ave, Scranton, PA 18503"),
    (1014, "Nancy Wheeler", 95.00, "processing", "212 Maple Ave, Hawkins, IN 46001"),
    (1015, "Oscar Martinez", 84.60, "delivered", "314 High St, Scranton, PA 18503"),
    (1016, "Peter Parker", 29.99, "processing", "20 Ingram St, Queens, NY 11375"),
    (1017, "Quinn Fabray", 145.00, "shipped", "415 Lima Ln, Lima, OH 45801"),
    (1018, "Rachel Green", 230.10, "delivered", "495 Grove St, New York, NY 10014"),
    (1019, "Steve Rogers", 60.00, "delivered", "569 Leaman Pl, Brooklyn, NY 11201"),
    (1020, "Tony Stark", 999.99, "processing", "10880 Malibu Point, Malibu, CA 90265")
]

def init_db(db_path: str = DB_PATH, reset: bool = True):
    """Initialize the SQLite database with orders and refunds tables."""
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()

    if reset:
        cursor.execute("DROP TABLE IF EXISTS tickets")
        cursor.execute("DROP TABLE IF EXISTS refunds")
        cursor.execute("DROP TABLE IF EXISTS orders")

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS orders (
            id INTEGER PRIMARY KEY,
            customer TEXT NOT NULL,
            amount REAL NOT NULL,
            status TEXT NOT NULL,
            address TEXT NOT NULL
        )
    """)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS refunds (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            order_id INTEGER NOT NULL,
            amount REAL NOT NULL,
            reason TEXT,
            idempotency_key TEXT UNIQUE,
            timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY(order_id) REFERENCES orders(id)
        )
    """)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS tickets (
            ticket_id TEXT PRIMARY KEY,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            user_input TEXT,
            reason TEXT,
            status TEXT DEFAULT 'open',
            steps_count INTEGER,
            cost_usd REAL,
            trace_id TEXT,
            conversation_history TEXT
        )
    """)

    cursor.executemany("""
        INSERT INTO orders (id, customer, amount, status, address)
        VALUES (?, ?, ?, ?, ?)
    """, SAMPLE_ORDERS)

    conn.commit()
    conn.close()
    print(f"Database initialized successfully at: {db_path} with {len(SAMPLE_ORDERS)} orders.")

if __name__ == "__main__":
    init_db()

