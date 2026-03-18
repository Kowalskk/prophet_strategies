import pandas as pd
import shutil
import os
from pathlib import Path

def generate_summary():
    csv_path = Path("output/csv/master_results.csv")
    temp_path = Path("output/csv/master_results_temp_analysis.csv")
    output_path = Path("output/csv/summary_for_claude.txt")
    
    if not csv_path.exists():
        print(f"File not found: {csv_path}")
        return

    print("Safely copying master_results.csv to avoid locking...")
    shutil.copy(csv_path, temp_path)
    
    try:
        df = pd.read_csv(temp_path)
        print(f"Loaded {len(df)} rows from file.")
        
        # Deduplicate just in case
        df = df.drop_duplicates()
        
        # 1. Total overview
        total_combos = 22950
        progress = (len(df) / total_combos) * 100
        summary_text = f"=== PROPHET STRATEGIES SUMMARY ===\n"
        summary_text += f"Total Progress: {len(df):,} / {total_combos:,} ({progress:.2f}%)\n\n"

        # 2. Aggregates by Strategy & Crypto
        summary_text += "--- 📊 AVERAGE METRICS BY STRATEGY & CRYPTO ---\n"
        agg = df.groupby(['strategy', 'crypto']).agg({
            'roi_pct': 'mean',
            'win_rate': 'mean',
            'total_trades': 'mean'
        }).reset_index()
        summary_text += agg.to_string(index=False) + "\n\n"

        # 3. Top 3 Results PER Crypto & Strategy
        summary_text += "--- 🏆 TOP 3 CONFIGURATIONS BY ROI PER CRYPTO ---\n"
        df_sorted = df.sort_values('roi_pct', ascending=False)
        
        # Columns to display nicely
        params_cols = [c for c in df.columns if c.startswith("param_")]
        display_cols = ['strategy', 'crypto', 'roi_pct', 'win_rate', 'total_trades'] + params_cols

        for crypto in df['crypto'].unique():
            summary_text += f"\n👉 {crypto}\n"
            top_c = df_sorted[df_sorted['crypto'] == crypto].head(3)
            # Make params readable
            for i, row in top_c.iterrows():
                p_dict = {k.replace("param_",""): v for k, v in row.items() if k.startswith("param_") and pd.notna(v)}
                summary_text += f"  - ROI: {row['roi_pct']:.2f}% | WinRate: {row['win_rate']:.4f} | Trades: {len(df_sorted)} (total df size)\n"
                summary_text += f"    Params: {p_dict}\n"

        print(f"\nSaving to {output_path}...")
        with open(output_path, 'w', encoding='utf-8') as f:
            f.write(summary_text)
            
        print("\n" + "="*40)
        print("📋 CONTENT TO COPY TO CLAUDE:")
        print("="*40)
        print(summary_text)
        print("="*40)
            
    finally:
        if temp_path.exists():
            os.remove(temp_path)

if __name__ == "__main__":
    generate_summary()
