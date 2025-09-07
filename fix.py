import sqlite3

def add_price_per_piece_column():
    db_path = 'instance/egg_store.db'
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()

    try:
        # Add price_per_piece column
        cursor.execute("ALTER TABLE product ADD COLUMN price_per_piece NUMERIC(10, 2) DEFAULT 0")
        print("Added 'price_per_piece' column.")
    except sqlite3.OperationalError as e:
        if "duplicate column name" in str(e):
            print("'price_per_piece' column already exists.")
        else:
            raise

    conn.commit()
    conn.close()

if __name__ == '__main__':
    add_price_per_piece_column()
