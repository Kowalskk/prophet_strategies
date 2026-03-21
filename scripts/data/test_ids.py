import sqlite3

def check_counts():
    db_path = "data/prophet.db"
    conn = sqlite3.connect(db_path)
    c = conn.cursor()
    
    c.execute("SELECT COUNT(DISTINCT condition_id) FROM market_trades")
    t_count = c.fetchone()[0]
    
    c.execute("SELECT COUNT(DISTINCT condition_id) FROM market_resolutions")
    r_count = c.fetchone()[0]
    
    c.execute("""
        SELECT COUNT(*) FROM (
            SELECT DISTINCT condition_id FROM market_trades
            INTERSECT
            SELECT DISTINCT condition_id FROM market_resolutions
        )
    """)
    intersect = c.fetchone()[0]
    
    print(f"Unique Trades IDs: {t_count}")
    print(f"Unique Resolutions IDs: {r_count}")
    print(f"Intersecting IDs: {intersect}")
    
    # Check casing/lengths
    c.execute("SELECT condition_id FROM market_trades LIMIT 1")
    t_id = c.fetchone()[0]
    c.execute("SELECT condition_id FROM market_resolutions LIMIT 1")
    r_id = c.fetchone()[0]
    
    print(f"Trade ID Sample: '{t_id}' (len {len(t_id)})")
    print(f"Res ID Sample:   '{r_id}' (len {len(r_id)})")
    
    conn.close()

if __name__ == "__main__":
    check_counts()
