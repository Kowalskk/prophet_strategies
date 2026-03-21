# ARBIBOT: Sistema Dual de Trading en Polymarket
## Prompt Maestro para Arquitectura y Desarrollo

---

## 🎯 OBJETIVO GENERAL

Construir un **sistema de backtesting y trading automático** que ejecute **dos estrategias de arbitrage simultáneamente** en Polymarket (prediction markets de Ethereum diarios). El sistema debe:

1. **Descargar datos históricos** de Polymarket via Dune Analytics
2. **Ejecutar backtests** con ambas estrategias en paralelo
3. **Permitir ajuste dinámico** de spreads, capital, y parámetros
4. **Generar reports** detallados de P&L, win rate, Sharpe ratio
5. **Ser escalable** para agregar nuevas estrategias

---

## 📊 ESTRATEGIAS A IMPLEMENTAR

### **ESTRATEGIA 1: StinkBot Pro (Saulo's Custom)**
**Descripción:** Placing ultra-cheap discrete limit orders en precios específicos y farming volatilidad inicial.

**Parámetros principales:**
```
- spread_si_cents: 0.002 (compra SI a $0.002)
- spread_no_cents: 0.03 (compra NO a $0.03)
- capital_per_trade: $50 (NO) + $3 (SI) = $53
- hold_duration_minutes: 5-60
- target_pnl_percent: 50+ (ganancia esperada si llena)
- max_slippage_percent: 2
```

**Lógica:**
1. Esperar opening del mercado diario (17:01 UTC)
2. Colocar 4 órdenes límite: 2×NO a spread_no_cents, 2×SI a spread_si_cents
3. Cuando llena cualquiera → exit inmediato
4. Calcular P&L real con fees

---

### **ESTRATEGIA 2: Volatility Stink Bid (Claude's Proposal)**
**Descripción:** Placing orders a spreads más amplios (% basado) alrededor del opening price y esperando que la volatilidad llene ambas.

**Parámetros principales:**
```
- spread_percent: 5 (±5% del opening price)
- capital_per_trade: $300 (configurable)
- hold_duration_minutes: 1-15
- both_sides_fill_required: false (si 1 lado llena es ganancia)
- target_pnl_percent: 30-50
```

**Lógica:**
1. Esperar opening, calcular opening_price
2. Colocar: BID_BUY = opening_price - 5%, BID_SELL = opening_price + 5%
3. Exit cuando ambas llenan O cuando 1 lado alcanza target P&L
4. Calcular P&L con fees + slippage

---

## 🛠️ ARQUITECTURA TÉCNICA REQUERIDA

### **Componentes principales:**

```
arbibot-system/
├── data/
│   ├── dune_extractor.py      # Extract data from Dune Analytics
│   ├── market_cleaner.py       # Clean & normalize Polymarket data
│   └── historical_db.db        # SQLite with historical trades
│
├── backtest/
│   ├── strategy_base.py        # Abstract base class
│   ├── stinky_bot.py           # Strategy 1 implementation
│   ├── volatility_bot.py       # Strategy 2 implementation
│   ├── backtest_engine.py      # Orchestrate backtest execution
│   └── simulator.py            # Order fill simulation
│
├── analysis/
│   ├── metrics.py              # Sharpe, Sortino, Max DD, Win Rate
│   ├── report_generator.py     # PDF/CSV reports with charts
│   └── sensitivity_analyzer.py # Parameter optimization
│
├── live/ (future)
│   ├── order_executor.py       # Connect to Polymarket API
│   ├── websocket_monitor.py    # Real-time price updates
│   └── risk_manager.py         # Position sizing, drawdown alerts
│
├── config/
│   ├── strategies_config.yaml  # All strategy parameters
│   └── backtest_config.yaml    # Backtest settings
│
├── main.py                     # CLI interface
└── requirements.txt
```

---

## 📈 FUNCIONALIDADES CLAVE

### **1. Data Management**
- ✅ Query Dune Analytics para datos históricos de Polymarket (últimos 6 meses)
- ✅ Cache datos en SQLite para no re-queryear
- ✅ Actualizar datos diarios (nuevos mercados cada día)
- ✅ Validar data integridad (no gaps, fechas consistentes)

### **2. Backtesting Engine**
- ✅ Simular order fills basado en historical min/max prices
- ✅ Aplicar slippage realista (2-3%)
- ✅ Aplicar fees de Polymarket (2% por transacción)
- ✅ Ejecutar ambas estrategias en PARALELO sobre los MISMOS mercados
- ✅ Detallado trade-by-trade log con timestamps

### **3. Metrics & Reporting**
Para cada backtest:
- **Aggregate metrics:**
  - Total P&L ($)
  - Total Return (%)
  - Win Rate (%)
  - Sharpe Ratio
  - Max Drawdown
  - Avg Win / Avg Loss
  - Profit Factor

- **Per-strategy comparison:**
  - Side-by-side P&L
  - Correlation of returns
  - Best/Worst day for each
  - Win streak analysis

- **Outputs:**
  - CSV con todos los trades
  - HTML interactive dashboard
  - PNG charts (equity curve, monthly returns, etc.)

### **4. Parameter Adjustment Interface**
**CLI interactivo para ajustar:**
```bash
$ python main.py backtest
  > Strategy: [1: StinkyBot, 2: VolatilityBot, 3: Both]
  > Date range: [YYYY-MM-DD to YYYY-MM-DD]
  
  > Strategy 1 params:
    - spread_si_cents: [0.001 to 0.01] default=0.002
    - spread_no_cents: [0.01 to 0.10] default=0.03
    - capital: [10 to 1000] default=53
  
  > Strategy 2 params:
    - spread_percent: [1 to 20] default=5
    - capital: [100 to 10000] default=300
  
  > Simulation settings:
    - slippage_percent: [0 to 5] default=2.5
    - fee_percent: [0 to 3] default=2
    - fill_probability: [50 to 100] default=60 (% chance order fills)
```

**Outputs:**
- Quick result summary in terminal
- Full backtest report saved to `results/backtest_YYYY_MM_DD_HHMM.html`
- CSV trade log for further analysis

### **5. Sensitivity Analysis**
- **Grid search** sobre múltiples parámetros
- **Heatmap:** spread_si vs spread_no → P&L
- **Heatmap:** capital vs spread_percent → Win Rate
- Identificar **optimal parameter combinations**

---

## 💾 DATA SCHEMA

### **Markets Table (from Dune)**
```
market_id (PK)
question
token_outcome (YES/NO)
market_start_time
market_end_time
resolved_on_timestamp
min_price_first_hour
max_price_first_hour
avg_price_first_hour
volume_usd
num_trades
```

### **Backtest Results Table (SQLite)**
```
trade_id (PK)
strategy_name
market_id (FK)
entry_price
entry_time
exit_price
exit_time
capital_deployed
p_and_l_dollars
p_and_l_percent
fees_paid
slippage_applied
status (FILLED, PARTIAL, MISSED)
notes
```

---

## 🔧 REQUISITOS TÉCNICOS

### **Libraries:**
```
pandas>=2.0
numpy>=1.24
matplotlib>=3.7
seaborn>=0.12
sqlite3 (built-in)
requests>=2.31  # For Dune API
pyyaml>=6.0     # Config files
click>=8.1      # CLI
jinja2>=3.1     # Report templates
pytz>=2023.3    # Timezone handling
```

### **API Access:**
- Dune Analytics API key (provided)
- Polymarket data endpoint (read-only for backtesting)

### **Performance:**
- Backtest 6 months of data (180+ markets) in <5 seconds
- Sensitivity analysis (10×10 grid) in <30 seconds

---

## 🎬 USER WORKFLOWS

### **Workflow 1: Quick Backtest**
```bash
$ python main.py backtest --strategy both --days 30
> Results saved to: results/backtest_2024_03_09_1425.html
> Summary:
  Strategy 1 P&L: +$2,450 (122 ROI%)
  Strategy 2 P&L: +$1,890 (95 ROI%)
  Combined: +$4,340
```

### **Workflow 2: Parameter Tuning**
```bash
$ python main.py tune --strategy 1 --param spread_si_cents [0.001,0.002,0.005]
> Grid search in progress...
> Best parameters: spread_si=0.002, spread_no=0.03
> Estimated P&L: +$3,200
```

### **Workflow 3: Sensitivity Heatmap**
```bash
$ python main.py sensitivity --strategy 2 --param1 spread_percent --param2 capital
> Generating heatmap...
> Saved to: results/heatmap_volatility_bot.png
> Optimal combo: spread=7%, capital=$400 → P&L=$2,100
```

---

## 🎯 PRÓXIMOS PASOS

### **Phase 1: Core System (Week 1)**
- [ ] Data extraction from Dune
- [ ] SQLite schema & ingestion
- [ ] Strategy base classes
- [ ] Basic backtest engine
- [ ] Simple CSV output

### **Phase 2: Analysis & Reporting (Week 2)**
- [ ] Metrics calculation
- [ ] HTML dashboard generator
- [ ] Charts (equity curve, etc.)
- [ ] Parameter tuning CLI

### **Phase 3: Advanced Features (Week 3+)**
- [ ] Sensitivity analysis
- [ ] Multi-strategy correlation
- [ ] Monte Carlo simulation
- [ ] Live trading connector (Polymarket API)

---

## 🚀 SUCCESS CRITERIA

✅ **Backtesting:**
- Reproduce your exact $50 + $3 = $1,500+ P&L example
- Both strategies run on same data
- Comparable, realistic numbers

✅ **Flexibility:**
- Adjust all spreads/capital without code changes
- Run 100+ backtest variations in <5 min

✅ **Reporting:**
- Clear winner identification
- P&L attribution by strategy
- Parameter sensitivity obvious

---

## 📝 NOTES FOR CLAUDE

1. **Use async I/O** where possible (Dune API calls, file ops)
2. **Modular design** - each strategy is independent plugin
3. **Realistic simulation:**
   - Min/max prices from historical data
   - Random fill probability (60-80% default)
   - Slippage based on order size vs volume
4. **User-friendly:**
   - No code changes needed for testing variations
   - Progress bars for long operations
   - Clear error messages

---

## 🔗 CONTEXT FILES

Pass Claude these supporting docs:
1. `Dune_Polymarket_Schema.md` - Exact table structure
2. `Polymarket_Fee_Structure.md` - Fee calculations
3. `Historical_Backtest_Data_Sample.csv` - Example data

---

**Ready to build? Pass this prompt to Claude Code and request the full system architecture.**
