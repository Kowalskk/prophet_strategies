import yaml
import yaml
from backtest.grid_runner import GridRunner, _job_key, _row_key, _load_csv
from pathlib import Path

def debug():
    # 1. Load config
    cfg_dict = yaml.safe_load(open('config/config.yaml'))
    # Use a simpler load if Config doesn't work well due to imports
    print("Config dict loaded")
    
    runner = GridRunner(cfg_dict) # Passing dict if Config fails in run_vps.py
    
    csv_path = Path("output/csv/master_results.csv")
    if csv_path.exists():
        rows = _load_csv(csv_path)
        print(f"Loaded {len(rows)} rows from CSV")
        if rows:
            print("\nComparing keys for first row:")
            row = rows[0]
            r_key = _row_key(row)
            print(f"Row key: {r_key}")
            
            # Let's generate all jobs to find the match
            # But just print some job keys to see formatting
            jobs = list(runner._all_jobs())
            print(f"Generated {len(jobs)} total jobs")
            
            # Check if any job key matches
            test_job = jobs[0]
            j_key = _job_key(test_job)
            print(f"Job key example: {j_key}")
            
            matches = 0
            for j in jobs:
                if _job_key(j) == r_key:
                    matches += 1
            print(f"Total jobs matching first row key: {matches}")
            
            # Let's see some params formats
            # Row params
            p_row = {k.replace("param_", ""): v for k, v in row.items() if k.startswith("param_")}
            # Job params
            p_job = test_job["params"]
            
            print("\nRow Params formats:")
            for k,v in p_row.items():
                 print(f"  {k}: {v} ({type(v)})")
            print("\nJob Params formats:")
            for k,v in p_job.items():
                 print(f"  {k}: {v} ({type(v)})")

if __name__ == "__main__":
    debug()
