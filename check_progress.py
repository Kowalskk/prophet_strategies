import pandas as pd
import os
import time

def check():
    log_path = "output/prophet.log"
    csv_path = "output/csv/master_results.csv"
    
    print("=== PROPHET STRATEGIES MONITOR ===")
    
    # Check logs
    if os.path.exists(log_path):
        print("\nLast 5 log lines:")
        with open(log_path, "r") as f:
            lines = f.readlines()
            for line in lines[-5:]:
                print(f"  {line.strip()}")
    
    # Check results
    if os.path.exists(csv_path):
        try:
            df = pd.read_csv(csv_path)
            total_expected = 22950
            completed = len(df)
            progress = (completed / total_expected) * 100
            print(f"\n[PROGRESS] {completed:,} / {total_expected:,} combinations completed ({progress:.2f}%)")
            
            if completed > 0:
                print("\nTop 5 results by ROI (Ranked):")
                top = df.sort_values('roi_pct', ascending=False).head(5)
                # Filter to interesting columns
                cols = ['strategy', 'crypto', 'roi_pct', 'win_rate', 'total_trades']
                print(top[cols].to_string(index=False))
                    
                # Calculate simple ETA
                # (This is rough as it doesn't know start time exactly)
                print(f"\nETA: Approximately {((total_expected - completed) * 5.5) / 3600:.1f} hours remaining at 2 workers.")
        except Exception as e:
            print(f"\nResults file found but currently busy: {e}")
    else:
        print("\nSimulation status: INITIALIZING (Preloading 12.6M trades into memory...)")
        print("Note: The 'master_results.csv' is created after the first 100 backtests are finished.")

if __name__ == "__main__":
    check()
