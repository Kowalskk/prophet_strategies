"""
PROPHET STRATEGIES
Report — generates a self-contained HTML dashboard from grid search results
"""
from __future__ import annotations
import base64
import io
import logging
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.ticker as mticker
import seaborn as sns

from analysis.optimizer import rank_results, best_per_strategy
from analysis.export import _coerce_numerics

logger = logging.getLogger(__name__)

sns.set_theme(style="whitegrid", palette="muted")
plt.rcParams.update({
    "font.family": "DejaVu Sans",
    "axes.spines.top": False,
    "axes.spines.right": False,
})

PURPLE  = "#7C3AED"
GREEN   = "#059669"
RED     = "#DC2626"
BLUE    = "#2563EB"
ORANGE  = "#D97706"
GRAY    = "#6B7280"
BG      = "#0D1117"
CARD_BG = "#161B22"

EXIT_ORDER = [
    "hold_to_resolution",
    "sell_at_2x", "sell_at_5x", "sell_at_10x", "sell_at_15x",
    "sell_at_25x", "sell_at_50x", "sell_at_75x", "sell_at_100x",
    "sell_at_125x", "sell_at_150x",
]


def _fig_to_b64(fig) -> str:
    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=130, bbox_inches="tight",
                facecolor=fig.get_facecolor())
    plt.close(fig)
    return base64.b64encode(buf.getvalue()).decode()


class ReportGenerator:
    """Generates a self-contained HTML dashboard from master_results.csv."""

    def __init__(self, output_dir: str = "output"):
        self.output_dir = Path(output_dir)
        (self.output_dir / "reports").mkdir(parents=True, exist_ok=True)

    def generate(self, results_csv: str) -> Path:
        df = pd.read_csv(results_csv)
        _coerce_numerics(df)
        logger.info(f"Generating HTML report from {len(df):,} results")

        charts = {
            "pnl_dist":      self._chart_pnl_distribution(df),
            "exit_heatmap":  self._chart_exit_heatmap(df),
            "sharpe_vs_pnl": self._chart_sharpe_vs_pnl(df),
            "by_crypto":     self._chart_by_crypto(df),
            "fill_compare":  self._chart_fill_model_compare(df),
            "winrate_pf":    self._chart_winrate_vs_pf(df),
        }

        html = self._render_html(df, charts)
        path = self.output_dir / "reports" / "dashboard.html"
        path.write_text(html, encoding="utf-8")
        logger.info(f"HTML dashboard -> {path}")
        return path

    # ------------------------------------------------------------------ Charts

    def _chart_pnl_distribution(self, df: pd.DataFrame) -> str:
        fig, axes = plt.subplots(1, 2, figsize=(12, 4), facecolor=CARD_BG)
        for ax in axes:
            ax.set_facecolor(CARD_BG)
            for spine in ax.spines.values():
                spine.set_edgecolor("#30363D")

        data = df["total_net_pnl"].dropna()
        axes[0].hist(data, bins=40, color=PURPLE, alpha=0.85, edgecolor="none")
        axes[0].axvline(0, color=RED, lw=1.5, linestyle="--", alpha=0.7)
        axes[0].axvline(data.median(), color=GREEN, lw=1.5, linestyle="--", alpha=0.7,
                        label=f"Median ${data.median():,.0f}")
        axes[0].set_title("Net P&L Distribution", color="white", fontsize=12, pad=8)
        axes[0].set_xlabel("Net P&L ($)", color=GRAY, fontsize=9)
        axes[0].set_ylabel("Count", color=GRAY, fontsize=9)
        axes[0].tick_params(colors=GRAY, labelsize=8)
        axes[0].legend(fontsize=8, labelcolor="white", facecolor=CARD_BG, edgecolor="#30363D")
        axes[0].xaxis.set_major_formatter(mticker.FuncFormatter(lambda x, _: f"${x:,.0f}"))

        sharpe = df["sharpe_ratio"].dropna()
        axes[1].hist(sharpe, bins=40, color=BLUE, alpha=0.85, edgecolor="none")
        axes[1].axvline(0, color=RED, lw=1.5, linestyle="--", alpha=0.7)
        axes[1].axvline(sharpe.median(), color=GREEN, lw=1.5, linestyle="--", alpha=0.7,
                        label=f"Median {sharpe.median():.3f}")
        axes[1].set_title("Sharpe Ratio Distribution", color="white", fontsize=12, pad=8)
        axes[1].set_xlabel("Sharpe Ratio", color=GRAY, fontsize=9)
        axes[1].set_ylabel("Count", color=GRAY, fontsize=9)
        axes[1].tick_params(colors=GRAY, labelsize=8)
        axes[1].legend(fontsize=8, labelcolor="white", facecolor=CARD_BG, edgecolor="#30363D")

        fig.tight_layout(pad=2)
        return _fig_to_b64(fig)

    def _chart_exit_heatmap(self, df: pd.DataFrame) -> str:
        if "param_exit_strategy" not in df.columns:
            return ""

        fig, axes = plt.subplots(1, 2, figsize=(14, 5), facecolor=CARD_BG)
        for ax in axes:
            ax.set_facecolor(CARD_BG)

        metrics = ["total_net_pnl", "sharpe_ratio"]
        titles  = ["Avg Net P&L ($)", "Avg Sharpe Ratio"]
        cmaps   = ["RdYlGn", "RdYlBu"]

        for i, (metric, title, cmap) in enumerate(zip(metrics, titles, cmaps)):
            pivot = df.groupby(["param_exit_strategy", "crypto"])[metric].mean().unstack()
            valid = [e for e in EXIT_ORDER if e in pivot.index]
            pivot = pivot.loc[valid]
            pivot.index = [e.replace("hold_to_resolution", "hold")
                            .replace("sell_at_", "") for e in pivot.index]

            sns.heatmap(
                pivot, ax=axes[i], cmap=cmap, annot=True,
                fmt=".0f" if metric == "total_net_pnl" else ".3f",
                linewidths=0.5, linecolor="#30363D",
                cbar_kws={"shrink": 0.8},
                annot_kws={"size": 8},
            )
            axes[i].set_title(f"{title} by Exit x Crypto",
                              color="white", fontsize=11, pad=8)
            axes[i].set_xlabel("Crypto", color=GRAY, fontsize=9)
            axes[i].set_ylabel("Exit Strategy", color=GRAY, fontsize=9)
            axes[i].tick_params(colors=GRAY, labelsize=8)

        fig.tight_layout(pad=2)
        return _fig_to_b64(fig)

    def _chart_sharpe_vs_pnl(self, df: pd.DataFrame) -> str:
        top = rank_results(df, top_n=200)
        if top.empty:
            return ""

        fig, ax = plt.subplots(figsize=(10, 5), facecolor=CARD_BG)
        ax.set_facecolor(CARD_BG)
        for spine in ax.spines.values():
            spine.set_edgecolor("#30363D")

        colors = {"BTC": ORANGE, "ETH": BLUE, "SOL": PURPLE}
        for crypto, grp in top.groupby("crypto"):
            ax.scatter(grp["sharpe_ratio"], grp["total_net_pnl"],
                       c=colors.get(crypto, GRAY), alpha=0.6, s=25,
                       label=crypto, edgecolors="none")

        ax.axhline(0, color=RED, lw=1, linestyle="--", alpha=0.5)
        ax.axvline(0, color=RED, lw=1, linestyle="--", alpha=0.5)
        ax.set_title("Sharpe Ratio vs Net P&L (Top 200)", color="white", fontsize=12, pad=8)
        ax.set_xlabel("Sharpe Ratio", color=GRAY, fontsize=9)
        ax.set_ylabel("Net P&L ($)", color=GRAY, fontsize=9)
        ax.tick_params(colors=GRAY, labelsize=8)
        ax.yaxis.set_major_formatter(mticker.FuncFormatter(lambda x, _: f"${x:,.0f}"))
        ax.legend(fontsize=9, labelcolor="white", facecolor=CARD_BG, edgecolor="#30363D")
        ax.text(0.98, 0.98, "Best Zone ->", transform=ax.transAxes,
                ha="right", va="top", color=GREEN, fontsize=9, alpha=0.7)

        fig.tight_layout(pad=1.5)
        return _fig_to_b64(fig)

    def _chart_by_crypto(self, df: pd.DataFrame) -> str:
        fig, axes = plt.subplots(1, 3, figsize=(13, 4), facecolor=CARD_BG)
        cryptos  = ["BTC", "ETH", "SOL"]
        c_colors = [ORANGE, BLUE, PURPLE]

        for ax, crypto, color in zip(axes, cryptos, c_colors):
            ax.set_facecolor(CARD_BG)
            for spine in ax.spines.values():
                spine.set_edgecolor("#30363D")

            sub = df[df["crypto"] == crypto]
            if sub.empty:
                ax.text(0.5, 0.5, "No data", ha="center", va="center",
                        color=GRAY, transform=ax.transAxes)
                ax.set_title(crypto, color="white")
                continue

            values  = [sub["total_net_pnl"].mean(),
                       sub["sharpe_ratio"].mean(),
                       sub["win_rate"].mean()]
            labels  = ["Avg P&L", "Avg Sharpe", "Avg Win%"]
            bcolors = [GREEN if v >= 0 else RED for v in values]

            bars = ax.bar(labels, [abs(v) for v in values],
                          color=bcolors, alpha=0.85, width=0.5)
            ax.set_title(crypto, color="white", fontsize=12, pad=6)
            ax.tick_params(colors=GRAY, labelsize=8)

            for bar, val in zip(bars, values):
                ax.text(bar.get_x() + bar.get_width() / 2,
                        bar.get_height() * 1.03,
                        f"{val:.1f}", ha="center", va="bottom",
                        color="white", fontsize=8)

        fig.suptitle("Performance by Crypto Asset", color="white", fontsize=13, y=1.02)
        fig.tight_layout(pad=1.5)
        return _fig_to_b64(fig)

    def _chart_fill_model_compare(self, df: pd.DataFrame) -> str:
        fig, axes = plt.subplots(1, 2, figsize=(11, 4), facecolor=CARD_BG)
        for ax in axes:
            ax.set_facecolor(CARD_BG)
            for spine in ax.spines.values():
                spine.set_edgecolor("#30363D")

        models   = df["fill_model"].unique() if "fill_model" in df.columns else []
        mcolors  = [PURPLE, BLUE, GREEN, ORANGE]
        metrics  = [("total_net_pnl", "Avg Net P&L ($)"),
                    ("sharpe_ratio",  "Avg Sharpe")]

        for ax, (metric, label) in zip(axes, metrics):
            vals = [df[df["fill_model"] == m][metric].mean() for m in models]
            bars = ax.bar(models, vals,
                          color=[mcolors[i % len(mcolors)] for i in range(len(models))],
                          alpha=0.85, width=0.4)
            ax.set_title(label, color="white", fontsize=11, pad=6)
            ax.tick_params(colors=GRAY, labelsize=9)
            ax.axhline(0, color=RED, lw=1, linestyle="--", alpha=0.5)
            for bar, val in zip(bars, vals):
                ax.text(bar.get_x() + bar.get_width() / 2,
                        bar.get_height() + abs(bar.get_height()) * 0.04,
                        f"{val:.2f}", ha="center", va="bottom",
                        color="white", fontsize=9)

        fig.suptitle("Optimistic vs Realistic Fill Model",
                     color="white", fontsize=12, y=1.02)
        fig.tight_layout(pad=1.5)
        return _fig_to_b64(fig)

    def _chart_winrate_vs_pf(self, df: pd.DataFrame) -> str:
        top = rank_results(df, top_n=300)
        if top.empty:
            return ""

        fig, ax = plt.subplots(figsize=(9, 5), facecolor=CARD_BG)
        ax.set_facecolor(CARD_BG)
        for spine in ax.spines.values():
            spine.set_edgecolor("#30363D")

        sc = ax.scatter(top["win_rate"], top["profit_factor"],
                        c=top["total_net_pnl"], cmap="RdYlGn",
                        alpha=0.65, s=20, edgecolors="none")
        cbar = fig.colorbar(sc, ax=ax, shrink=0.8)
        cbar.set_label("Net P&L ($)", color=GRAY, fontsize=8)
        cbar.ax.tick_params(colors=GRAY, labelsize=7)

        ax.set_title("Win Rate vs Profit Factor", color="white", fontsize=12, pad=8)
        ax.set_xlabel("Win Rate (%)", color=GRAY, fontsize=9)
        ax.set_ylabel("Profit Factor", color=GRAY, fontsize=9)
        ax.tick_params(colors=GRAY, labelsize=8)

        fig.tight_layout(pad=1.5)
        return _fig_to_b64(fig)

    # ------------------------------------------------------------------ HTML

    def _render_html(self, df: pd.DataFrame, charts: dict) -> str:
        _coerce_numerics(df)
        top   = rank_results(df, top_n=20)
        best  = best_per_strategy(df)
        kpis  = self._compute_kpis(df)
        ts    = datetime.now().strftime("%Y-%m-%d %H:%M")

        top_rows  = self._table_rows(top,  ["strategy","crypto","fill_model",
                                            "total_net_pnl","roi_pct","sharpe_ratio",
                                            "win_rate","profit_factor","max_drawdown",
                                            "composite_score","param_exit_strategy"])
        best_rows = self._table_rows(best, ["strategy","crypto","fill_model",
                                            "total_net_pnl","sharpe_ratio",
                                            "win_rate","profit_factor","composite_score"])

        kpi_html = "".join(
            f'<div class="kpi-card"><div class="kpi-label">{lb}</div>'
            f'<div class="kpi-value">{vl}</div>'
            f'<div class="kpi-sub">{sb}</div></div>'
            for lb, vl, sb in kpis
        )

        def img(key: str) -> str:
            b64 = charts.get(key, "")
            if not b64:
                return "<p style='color:#6B7280'>Chart unavailable</p>"
            return f'<img src="data:image/png;base64,{b64}" style="width:100%;border-radius:8px">'

        return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1.0">
<title>Prophet Strategies — Dashboard</title>
<style>
*{{box-sizing:border-box;margin:0;padding:0}}
body{{background:{BG};color:#E6EDF3;font-family:-apple-system,'Segoe UI',sans-serif;font-size:14px}}
.header{{background:linear-gradient(135deg,#161B22 0%,#0D1117 100%);border-bottom:1px solid #30363D;padding:24px 40px;display:flex;align-items:center;justify-content:space-between}}
.header h1{{font-size:22px;font-weight:700;color:white;letter-spacing:-0.3px}}
.header h1 span{{color:{PURPLE}}}
.main{{max-width:1400px;margin:0 auto;padding:32px 40px}}
.section{{margin-bottom:40px}}
.section-title{{font-size:16px;font-weight:700;color:white;border-left:3px solid {PURPLE};padding-left:10px;margin-bottom:16px}}
.kpi-grid{{display:grid;grid-template-columns:repeat(6,1fr);gap:12px;margin-bottom:32px}}
.kpi-card{{background:{CARD_BG};border:1px solid #30363D;border-radius:10px;padding:16px 12px;text-align:center}}
.kpi-label{{font-size:10px;color:#6B7280;font-weight:600;text-transform:uppercase;letter-spacing:.5px;margin-bottom:6px}}
.kpi-value{{font-size:22px;font-weight:800;color:white;margin-bottom:4px}}
.kpi-sub{{font-size:10px;color:#6B7280}}
.chart-grid{{display:grid;gap:16px}}
.chart-grid-2{{grid-template-columns:1fr 1fr}}
.chart-grid-1{{grid-template-columns:1fr}}
.chart-card{{background:{CARD_BG};border:1px solid #30363D;border-radius:10px;padding:16px}}
.chart-title{{font-size:11px;color:#6B7280;font-weight:600;text-transform:uppercase;letter-spacing:.5px;margin-bottom:10px}}
table{{width:100%;border-collapse:collapse;font-size:12px}}
thead th{{background:#161B22;color:{PURPLE};font-weight:700;padding:10px 8px;text-align:center;border-bottom:2px solid {PURPLE};white-space:nowrap;font-size:11px;text-transform:uppercase}}
tbody tr:nth-child(even){{background:rgba(255,255,255,.02)}}
tbody tr:hover{{background:rgba(124,58,237,.08)}}
tbody td{{padding:8px;text-align:center;border-bottom:1px solid #21262D;color:#C9D1D9}}
.pos{{color:{GREEN};font-weight:700}}
.neg{{color:{RED};font-weight:700}}
.tag{{display:inline-block;background:rgba(124,58,237,.15);color:{PURPLE};border-radius:4px;padding:2px 8px;font-size:11px;font-weight:600}}
.tag-btc{{background:rgba(217,119,6,.15);color:{ORANGE}}}
.tag-eth{{background:rgba(37,99,235,.15);color:{BLUE}}}
.tag-sol{{background:rgba(124,58,237,.15);color:{PURPLE}}}
.table-wrap{{background:{CARD_BG};border:1px solid #30363D;border-radius:10px;overflow:hidden}}
.footer{{text-align:center;color:#6B7280;font-size:11px;padding:24px;border-top:1px solid #21262D;margin-top:40px}}
</style>
</head>
<body>
<div class="header">
  <div>
    <h1>PROPHET <span>STRATEGIES</span></h1>
    <div style="color:#6B7280;font-size:12px;margin-top:4px">Polymarket Crypto Backtesting System</div>
  </div>
  <div style="color:#6B7280;font-size:12px">Generated: {ts} &nbsp;|&nbsp; {len(df):,} configurations tested</div>
</div>
<div class="main">

  <div class="section">
    <div class="kpi-grid">{kpi_html}</div>
  </div>

  <div class="section">
    <div class="section-title">Distribution Analysis</div>
    <div class="chart-grid chart-grid-1">
      <div class="chart-card"><div class="chart-title">P&amp;L and Sharpe Distributions</div>{img('pnl_dist')}</div>
    </div>
  </div>

  <div class="section">
    <div class="section-title">Exit Strategy Heatmap</div>
    <div class="chart-grid chart-grid-1">
      <div class="chart-card"><div class="chart-title">Avg P&amp;L and Sharpe by Exit Strategy x Crypto</div>{img('exit_heatmap')}</div>
    </div>
  </div>

  <div class="section">
    <div class="section-title">Risk / Return &amp; Fill Model</div>
    <div class="chart-grid chart-grid-2">
      <div class="chart-card"><div class="chart-title">Sharpe vs Net P&amp;L (Top 200)</div>{img('sharpe_vs_pnl')}</div>
      <div class="chart-card"><div class="chart-title">Win Rate vs Profit Factor</div>{img('winrate_pf')}</div>
    </div>
  </div>

  <div class="section">
    <div class="section-title">Asset &amp; Fill Model Breakdown</div>
    <div class="chart-grid chart-grid-2">
      <div class="chart-card"><div class="chart-title">Performance by Crypto</div>{img('by_crypto')}</div>
      <div class="chart-card"><div class="chart-title">Optimistic vs Realistic Fill Model</div>{img('fill_compare')}</div>
    </div>
  </div>

  <div class="section">
    <div class="section-title">Best Configuration per Strategy x Crypto x Fill Model</div>
    <div class="table-wrap">
      <table>
        <thead><tr><th>#</th><th>Strategy</th><th>Crypto</th><th>Fill</th>
          <th>Net P&amp;L</th><th>Sharpe</th><th>Win %</th>
          <th>Profit Factor</th><th>Score</th></tr></thead>
        <tbody>{best_rows}</tbody>
      </table>
    </div>
  </div>

  <div class="section">
    <div class="section-title">Top 20 Configurations — Ranked by Composite Score</div>
    <div class="table-wrap">
      <table>
        <thead><tr><th>#</th><th>Strategy</th><th>Crypto</th><th>Fill</th>
          <th>Net P&amp;L</th><th>ROI</th><th>Sharpe</th><th>Win %</th>
          <th>Prof. Factor</th><th>Drawdown</th><th>Score</th><th>Exit</th></tr></thead>
        <tbody>{top_rows}</tbody>
      </table>
    </div>
  </div>

</div>
<div class="footer">
  Prophet Strategies &nbsp;|&nbsp; Polymarket Crypto Backtesting
  &nbsp;|&nbsp; For research purposes only — not financial advice
</div>
</body>
</html>"""

    def _compute_kpis(self, df: pd.DataFrame) -> list:
        top       = rank_results(df, top_n=len(df))
        best_pnl  = df["total_net_pnl"].max() if len(df) else 0
        best_sh   = df["sharpe_ratio"].max()   if len(df) else 0
        best_wr   = df["win_rate"].max()        if len(df) else 0
        best_roi  = df["roi_pct"].max()         if len(df) else 0

        def fmt_pnl(v):
            if v >= 0:
                return f"<span style='color:{GREEN}'>${v:,.0f}</span>"
            return f"<span style='color:{RED}'>(${abs(v):,.0f})</span>"

        return [
            ("Total Configs",  f"{len(df):,}",     "all tested"),
            ("Pass Filters",   f"{len(top):,}",     ">=10 fills, >=5% rate"),
            ("Best Net P&L",   fmt_pnl(best_pnl),   "single config"),
            ("Best Sharpe",    f"{best_sh:.3f}",    "weekly"),
            ("Best Win Rate",  f"{best_wr:.1f}%",   "of filled trades"),
            ("Best ROI",       f"{best_roi:.1f}%",  "on capital"),
        ]

    def _table_rows(self, df: pd.DataFrame, cols: list) -> str:
        cols = [c for c in cols if c in df.columns]
        rows = []
        for i, (_, r) in enumerate(df.iterrows(), 1):
            cells = [f"<td>{i}</td>"]
            for col in cols:
                val = r.get(col)
                if col == "strategy":
                    label = "Stink Bid" if str(val) == "stink_bid" else "Vol Spread"
                    cells.append(f'<td><span class="tag">{label}</span></td>')
                elif col == "crypto":
                    css = f"tag-{str(val).lower()}"
                    cells.append(f'<td><span class="tag {css}">{val}</span></td>')
                elif col == "fill_model":
                    cells.append(f"<td>{val}</td>")
                elif col == "total_net_pnl":
                    v   = float(val) if pd.notna(val) else 0
                    css = "pos" if v >= 0 else "neg"
                    cells.append(f'<td class="{css}">${v:,.2f}</td>')
                elif col in ("roi_pct", "win_rate", "fill_rate"):
                    v = float(val) if pd.notna(val) else 0
                    cells.append(f"<td>{v:.1f}%</td>")
                elif col in ("sharpe_ratio", "profit_factor", "composite_score"):
                    v = float(val) if pd.notna(val) else 0
                    cells.append(f"<td>{v:.4f}</td>")
                elif col == "max_drawdown":
                    v = float(val) if pd.notna(val) else 0
                    cells.append(f"<td>${v:,.2f}</td>")
                elif col == "param_exit_strategy":
                    cells.append(f'<td><code style="font-size:10px">{val}</code></td>')
                else:
                    cells.append(f"<td>{val}</td>")
            rows.append(f"<tr>{''.join(cells)}</tr>")
        return "".join(rows)
