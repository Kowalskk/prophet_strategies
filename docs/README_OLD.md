# PROPHET STRATEGIES
## Polymarket Crypto Backtesting System

Sistema de backtesting multi-crypto para estrategias de stink bid y volatility spread en Polymarket.

---

## Setup

```bash
# 1. Instalar dependencias
pip install -r requirements.txt

# 2. Configurar API key de Dune
cp .env.example .env
# Editar .env con tu DUNE_API_KEY

# 3. Descargar datos
python -m data.data_manager --fetch --validate
```

---

## Arquitectura


# 4. Large-Scale Optimization (Grid Search)
python run_vps.py --workers 2
```

---

### 📊 Project Architecture
```text
prophet_strategies/
├── config/              # Centralized strategy & engine configuration
├── data/                # High-throughput Data Pipeline (Dune, SQLite, CoinGecko)
├── models/              # Optimized Dataclasses for memory efficiency
├── backtest/            # Core Engine & Strategy Implementations
├── analysis/            # Performance Metrics & Optimization Modules
└── output/              # Strategy ranking, CSV/XLSX exports & HTML reports
```

---

### 🏆 Current Development Status

| Module | Status | Highlights |
| :--- | :--- | :--- |
| **Data Pipeline** | ✅ Finished | 12.6M trades indexed in SQLite. |
| **Backtest Engine** | ✅ Finished | Sharded asset loading implemented. |
| **Grid Search** | 🧪 73% Complete | 16,800/22,950 combinations processed. |
| **Top ROI** | 🚀 780%+ | Verified initial high-yield configurations. |

---

### 📈 Strategy Performance
- **Stink Bid**: Targeted ROI ranges of 200% - 800% on volatile crypto markets.
- **Delta Neutral**: Capturing spread inefficiencies regardless of market direction.

---
*Developed for professional traders looking to institutionalize their Polymarket edge.*
