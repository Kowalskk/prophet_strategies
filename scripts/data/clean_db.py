import sqlite3
import logging
import argparse
import time
from tqdm import tqdm
from data.market_resolver import MarketParser
from models.market import Market
from data.data_manager import DataManager

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

def clean_database(db_path="data/prophet.db", execute=False, vacuum=False, row_chunk=5000, delay=0.5):
    conn = sqlite3.connect(db_path)
    c = conn.cursor()
    parser = MarketParser()

    # Get stats before
    c.execute("SELECT COUNT(*) FROM market_trades")
    trades_before = c.fetchone()[0]
    
    c.execute("SELECT COUNT(*) FROM markets")
    markets_before = c.fetchone()[0]

    c.execute("SELECT COUNT(*) FROM market_resolutions")
    resolutions_before = c.fetchone()[0]

    print(f"--- BEFORE CLEANUP ---")
    print(f"Trades: {trades_before:,}")
    print(f"Markets: {markets_before:,}")
    print(f"Resolutions: {resolutions_before:,}")
    print(f"----------------------\n")

    # 1. Index check
    print("Ensuring index exists for efficient lookup...")
    c.execute("CREATE INDEX IF NOT EXISTS idx_trades_question ON market_trades(question)")
    conn.commit()

    # 2. Find invalid questions
    print("Identifying invalid questions...")
    c.execute("SELECT DISTINCT question FROM market_trades WHERE question IS NOT NULL")
    all_questions = [row[0] for row in c.fetchall()]

    invalid_questions = set()
    for q in tqdm(all_questions, desc="Analyzing filters", ncols=80):
        m = Market(
            condition_id="tmp",
            question=q,
            event_market_name="",
            total_volume_usd=0,
            trade_count=0,
            neg_risk=False
        )
        parser.parse(m)
        if not m.is_parsed():
            invalid_questions.add(q)
            
    c.execute("SELECT DISTINCT question FROM markets WHERE question IS NOT NULL")
    for q in [row[0] for row in c.fetchall()]:
        if q not in invalid_questions:
            m = Market(condition_id="tmp", question=q, event_market_name="", total_volume_usd=0, trade_count=0, neg_risk=False)
            parser.parse(m)
            if not m.is_parsed():
                invalid_questions.add(q)

    invalid_questions = list(invalid_questions)
    print(f"\nFound {len(invalid_questions):,} invalid questions.")

    if execute:
        print(f"\nULTRA-SAFE EXECUTION: Deleting {row_chunk} rows at a time with {delay}s delay...")
        
        trades_deleted = 0
        for q in tqdm(invalid_questions, desc="Cleaning markets"):
            while True:
                # Find rowids to delete for this question
                c.execute(f"SELECT rowid FROM market_trades WHERE question = ? LIMIT {row_chunk}", (q,))
                rowids = [r[0] for r in c.fetchall()]
                
                if not rowids:
                    break
                    
                # Delete by rowid (much faster and predictable load)
                placeholders = ",".join(["?"] * len(rowids))
                c.execute(f"DELETE FROM market_trades WHERE rowid IN ({placeholders})", rowids)
                trades_deleted += c.rowcount
                conn.commit()
                
                if delay > 0:
                    time.sleep(delay)
            
            # Delete from markets (usually just 1 row per question, no need to chunk)
            c.execute("DELETE FROM markets WHERE question = ?", (q,))
            conn.commit()

        # Delete orphaned resolutions
        print("\nCleaning orphaned resolutions...")
        c.execute("""
            DELETE FROM market_resolutions 
            WHERE condition_id NOT IN (
                SELECT DISTINCT condition_id FROM market_trades
                UNION
                SELECT DISTINCT condition_id FROM markets
            )
        """)
        resolutions_deleted = c.rowcount
        conn.commit()
        
        if vacuum:
            print("Reclaiming space (VACUUM)...")
            # VACUUM is heavy, handle with care
            c.execute("VACUUM")
            conn.commit()

        print("\n--- AFTER CLEANUP ---")
        c.execute("SELECT COUNT(*) FROM market_trades")
        trades_after = c.fetchone()[0]
        print(f"Trades: {trades_after:,} (Deleted: {trades_before - trades_after:,})")
        
        print("\nValidation stats:")
        dm = DataManager(db_path=db_path)
        stats = dm.validate()
        for k, v in stats.items():
            print(f"  {k}: {v}")
        dm.close()
        
    else:
        print("\nRun with --execute to perform ULTRA-SAFE deletion.")
        print(f"Parameters: --row-chunk {row_chunk} --delay {delay}")

    conn.close()

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Prophet DB Ultra-Safe Cleaner")
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--vacuum", action="store_true")
    parser.add_argument("--row-chunk", type=int, default=5000)
    parser.add_argument("--delay", type=float, default=0.5)
    args = parser.parse_args()
    
    clean_database(execute=args.execute, vacuum=args.vacuum, row_chunk=args.row_chunk, delay=args.delay)
