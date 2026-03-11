import sqlite3

def list_tables():
    db_path = "data/prophet.db"
    conn = sqlite3.connect(db_path)
    c = conn.cursor()
    c.execute("SELECT name FROM sqlite_master WHERE type='table'")
    tables = [t[0] for t in c.fetchall()]
    print("Tables:", tables)
    
    for table in tables:
        c.execute(f"SELECT COUNT(*) FROM {table}")
        count = c.fetchone()[0]
        print(f"  {table}: {count:,} rows")
        
    conn.close()

if __name__ == "__main__":
    list_tables()
