import sqlite3
import json
import sys
import os

# Ensure we can import from the project
sys.path.insert(0, os.getcwd())
from data.market_resolver import parse_resolution

def fix_resolutions():
    db_path = "data/prophet.db"
    conn = sqlite3.connect(db_path)
    c = conn.cursor()
    
    print("Fetching resolutions...")
    c.execute("SELECT condition_id, payout_numerators FROM market_resolutions")
    rows = c.fetchall()
    
    print(f"Processing {len(rows):,} resolutions...")
    updates = []
    
    for cond_id, payout_str in rows:
        try:
            # payout_str is like "['1000...', '0']"
            # It's not valid JSON if it uses single quotes, so let's fix it
            payout_json = payout_str.replace("'", '"')
            payout = json.loads(payout_json)
            outcome = parse_resolution(payout)
            if outcome.value != "UNKNOWN":
                updates.append((outcome.value, cond_id))
        except Exception as e:
            continue
            
    print(f"Found {len(updates):,} valid resolutions (YES/NO). Updating DB...")
    
    c.executemany("UPDATE market_resolutions SET resolved_outcome = ? WHERE condition_id = ?", updates)
    conn.commit()
    print("Done fixing market_resolutions.")
    conn.close()

if __name__ == "__main__":
    fix_resolutions()
