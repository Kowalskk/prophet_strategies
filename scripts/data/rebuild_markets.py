import sys
import os
from pathlib import Path

# Ensure we can import from the project
sys.path.insert(0, os.getcwd())
from data.data_manager import DataManager

def rebuild():
    db_path = "data/prophet.db"
    dm = DataManager(db_path=db_path)
    count = dm.build_markets()
    print(f"Rebuilt {count:,} markets.")
    dm.close()

if __name__ == "__main__":
    rebuild()
