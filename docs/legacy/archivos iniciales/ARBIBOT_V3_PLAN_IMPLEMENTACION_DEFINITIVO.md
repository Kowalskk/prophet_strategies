# ARBIBOT v3: PLAN DE IMPLEMENTACIÓN DEFINITIVO
## Sistema de Backtesting Multi-Crypto para Polymarket Stink Bids
### Para ejecutar con Claude Sonnet en sesiones de código

**Autor: Claude Opus · 9 Marzo 2026**
**Datos verificados: Dune Analytics `polymarket_polygon.market_trades`**

---

## 1. CONTEXTO Y DATOS VERIFICADOS

### 1.1 Cómo funcionan los mercados (verificado con datos reales)

Los mercados de Polymarket para precio de crypto son tipo:
- **"Will the price of Bitcoin be above $74,000 on March 3?"**
- **"Will Ethereum hit $5,000 by December 31?"**

Cada mercado tiene dos tokens: **YES** y **NO**. YES + NO = $1.00 siempre.
El mercado resuelve en una **fecha específica** (semanal o con deadline).
La fuente de resolución es **Binance 1-minute candle HIGH/LOW**.

### 1.2 Liquidez verificada en Dune (últimos 90 días)

He consultado Dune y confirmado que hay liquidez REAL en los niveles de precio que nos interesan:

| Crypto | Rango Precio | Trades (90d) | Volumen USD | Mercados Distintos |
|--------|-------------|-------------|-------------|-------------------|
| **BTC** | < 0.5¢ | 577,534 | $464,970 | ~1,964 |
| **BTC** | 0.5-1¢ | 129,546 | $208,386 | ~1,914 |
| **BTC** | 1-3¢ | 342,161 | $1,055,909 | ~2,039 |
| **BTC** | 3-5¢ | 227,288 | $1,148,282 | ~1,932 |
| **ETH** | < 0.5¢ | 261,718 | $163,530 | ~2,007 |
| **ETH** | 0.5-1¢ | 55,561 | $78,850 | ~1,861 |
| **ETH** | 1-3¢ | 144,400 | $384,949 | ~2,093 |
| **ETH** | 3-5¢ | 81,863 | $384,257 | ~1,744 |
| **SOL** | < 0.5¢ | 104,322 | $45,622 | ~1,974 |
| **SOL** | 0.5-1¢ | 25,386 | $20,545 | ~1,699 |
| **SOL** | 1-3¢ | 59,904 | $96,401 | ~2,039 |
| **SOL** | 3-5¢ | 23,243 | $89,791 | ~1,268 |

**CONCLUSIÓN: Hay liquidez suficiente en los tres criptos a todos los niveles de stink bid.**
BTC tiene el mayor volumen, seguido de ETH, y SOL tiene menos pero suficiente.

### 1.3 Tabla principal de datos

```
polymarket_polygon.market_trades
├── block_time          -- timestamp del trade
├── tx_hash            -- hash de transacción
├── action             -- tipo de acción
├── condition_id       -- ID único del mercado
├── event_market_name  -- nombre del evento
├── question           -- pregunta completa del mercado
├── token_outcome      -- "Yes" | "No"
├── token_outcome_name -- nombre completo del outcome
├── neg_risk           -- "True" | "False"
├── asset_id           -- ID del token
├── price              -- precio del trade (0-1)
├── amount             -- volumen en USD
├── shares             -- número de shares
├── fee                -- fee pagado
├── maker              -- dirección maker
├── taker              -- dirección taker
└── polymarket_link    -- link al mercado
```

Tabla de resoluciones:
```
polymarket_polygon.ctf_evt_conditionresolution
├── conditionId         -- se cruza con condition_id de market_trades  
├── payoutNumerators    -- [1,0] = YES gana, [0,1] = NO gana
├── evt_block_time      -- cuándo resolvió
```

---

## 2. ESTRATEGIAS A BACKTESTEAR

### Estrategia A: "Stink Bid Extremo" (la del usuario)

**Concepto:** Para cada mercado semanal de precio crypto, colocar órdenes límite muy baratas en los niveles extremos (lejos del precio actual). Si alguna pega, la ganancia es masiva.

**Ejemplo concreto:**
```
BTC está a $80,000. Hay mercados semanales:
- "BTC above $74,000 on Mar 10" → YES a 95¢ / NO a 5¢
- "BTC above $76,000 on Mar 10" → YES a 85¢ / NO a 15¢  
- "BTC above $84,000 on Mar 10" → YES a 15¢ / NO a 85¢
- "BTC above $86,000 on Mar 10" → YES a 5¢ / NO a 95¢

TÚ COMPRAS:
1. "BTC above $74,000" → NO a 3¢ ($50) = apuesta a crash del 7.5%
2. "BTC above $86,000" → YES a 3¢ ($50) = apuesta a pump del 7.5%
3. "BTC above $72,000" → NO a 0.2¢ ($3) = apuesta a crash del 10%
4. "BTC above $88,000" → YES a 0.2¢ ($3) = apuesta a pump del 10%

Coste total: $106/semana
Si cualquiera pega: $1,000-1,667 de ganancia
```

**Parámetros a optimizar en el backtest:**
```yaml
stink_bid:
  # Niveles de precio de entrada (lo que pagas por share)
  tier1_price: [0.01, 0.02, 0.03, 0.04, 0.05]  # Grid search
  tier2_price: [0.001, 0.002, 0.003, 0.005]       # Grid search
  
  # Capital por tier
  tier1_capital: [25, 50, 75, 100]
  tier2_capital: [1, 3, 5, 10]
  
  # Distancia del precio actual (% move needed)
  tier1_distance_pct: [5, 7.5, 10]   # Calculado implícitamente
  tier2_distance_pct: [10, 12.5, 15]  # Calculado implícitamente
  
  # Exit strategy
  exit: ["hold_to_resolution", "sell_at_target"]
  sell_target_pct: [50, 100, 200, 500]  # % ganancia para vender antes
  
  # Crypto
  crypto: ["BTC", "ETH", "SOL"]
```

### Estrategia B: "Volatility Spread" (propuesta original)

**Concepto:** Para cada mercado, comprar el YES y NO en niveles simétricos alrededor del precio actual, capturando volatilidad.

**Ejemplo:**
```
BTC a $80,000:
- Comprar YES "above $84,000" a 5¢ ($150)   → apuesta a subida
- Comprar NO "above $76,000" a 5¢ ($150)    → apuesta a bajada

Si BTC se mueve ±5%, una de las dos se vuelve muy valiosa.
Si BTC se queda flat, pierdes ambas.
```

**Parámetros:**
```yaml
volatility_spread:
  spread_percent: [3, 5, 7, 10]
  entry_price_max: [0.03, 0.05, 0.07, 0.10]
  capital_per_side: [50, 100, 150, 200]
  exit: ["hold_to_resolution", "sell_at_target", "sell_if_both_fill"]
  sell_target_pct: [30, 50, 100]
  crypto: ["BTC", "ETH", "SOL"]
```

---

## 3. ARQUITECTURA DEL SISTEMA

```
arbibot/
│
├── config/
│   └── config.yaml              # Toda la configuración
│
├── data/
│   ├── dune_client.py           # Cliente Dune Analytics API
│   ├── data_manager.py          # Descarga, cache, validación
│   ├── market_resolver.py       # Determina resolución de mercados
│   └── price_fetcher.py         # Precio histórico BTC/ETH/SOL (CoinGecko/Binance)
│
├── models/
│   ├── market.py                # Dataclass Market
│   ├── trade.py                 # Dataclass Trade, TradeResult
│   └── config_models.py         # Dataclass para configs tipadas
│
├── backtest/
│   ├── engine.py                # Motor principal
│   ├── fill_simulator.py        # ¿Se habría llenado tu orden?
│   ├── fee_calculator.py        # Fees de Polymarket
│   ├── strategy_base.py         # ABC
│   ├── strategies/
│   │   ├── stink_bid.py         # Estrategia A
│   │   └── volatility_spread.py # Estrategia B
│   └── grid_runner.py           # Ejecuta backtests en grid paralelo
│
├── analysis/
│   ├── metrics.py               # Todas las métricas
│   ├── optimizer.py             # Grid search + best params
│   ├── export.py                # Export a CSV/XLSX
│   └── report.py                # Genera HTML reports
│
├── output/                      # Directorio para resultados
│   ├── csv/                     # Trade logs
│   ├── xlsx/                    # Spreadsheets con análisis
│   └── reports/                 # HTML dashboards
│
├── main.py                      # CLI principal
├── run_vps.py                   # Script para VPS (long-running)
├── requirements.txt
└── README.md
```

---

## 4. DATOS NECESARIOS Y CÓMO OBTENERLOS

### 4.1 Query Dune: Todos los mercados de precio crypto con trades

```sql
-- QUERY 1: Obtener todos los mercados de precio crypto con sus trades
-- Esto es lo que descargamos y cacheamos localmente
SELECT
    t.block_time,
    t.condition_id,
    t.question,
    t.event_market_name,
    t.token_outcome,
    t.token_outcome_name,
    t.price,
    t.amount,
    t.shares,
    t.fee,
    t.neg_risk,
    t.maker,
    t.taker
FROM polymarket_polygon.market_trades t
WHERE t.block_time >= CAST('2024-06-01' AS TIMESTAMP)
  AND (
    LOWER(t.question) LIKE '%bitcoin%' OR LOWER(t.question) LIKE '%btc%'
    OR LOWER(t.question) LIKE '%ethereum%' OR LOWER(t.question) LIKE '%eth%'
    OR LOWER(t.question) LIKE '%solana%' OR LOWER(t.question) LIKE '%sol%'
  )
  AND (
    LOWER(t.question) LIKE '%price%' OR LOWER(t.question) LIKE '%above%'
    OR LOWER(t.question) LIKE '%hit%' OR LOWER(t.question) LIKE '%below%'
  )
ORDER BY t.block_time ASC
```

### 4.2 Query Dune: Resoluciones de mercados

```sql
-- QUERY 2: Resoluciones - cuáles mercados resolvieron YES y cuáles NO
SELECT
    r.conditionId as condition_id,
    r.evt_block_time as resolution_time,
    r.payoutNumerators,
    CASE 
        WHEN r.payoutNumerators[1] = 1 THEN 'YES'
        WHEN r.payoutNumerators[2] = 1 THEN 'NO'
        ELSE 'UNKNOWN'
    END as resolved_outcome
FROM polymarket_polygon.ctf_evt_conditionresolution r
WHERE r.evt_block_time >= CAST('2024-06-01' AS TIMESTAMP)
```

### 4.3 Precios históricos de BTC/ETH/SOL

Necesitamos precios OHLC diarios de los cryptos para:
- Calcular qué % de movimiento representa cada nivel de mercado
- Determinar si un mercado "above $X" debería haber resuelto YES o NO
- Calcular la volatilidad realizada para cada período

**Fuente:** API de CoinGecko (gratuita) o Binance API
**Frecuencia:** Diaria (OHLC) + opcionalmente horaria para mayor precisión

### 4.4 Estructura de cache local (SQLite)

```sql
-- Tabla de mercados procesados
CREATE TABLE markets (
    condition_id TEXT PRIMARY KEY,
    question TEXT,
    crypto TEXT,                -- 'BTC' | 'ETH' | 'SOL'
    threshold_price REAL,       -- ej: 74000 para "above $74,000"
    market_type TEXT,           -- 'above' | 'below' | 'hit'
    resolution_date DATE,       -- fecha de resolución
    resolved_outcome TEXT,      -- 'YES' | 'NO' | NULL
    resolution_time TIMESTAMP,
    first_trade_time TIMESTAMP,
    last_trade_time TIMESTAMP,
    total_volume_usd REAL,
    num_trades INTEGER
);

-- Tabla de trades procesados
CREATE TABLE trades (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    condition_id TEXT,
    block_time TIMESTAMP,
    token_outcome TEXT,        -- 'Yes' | 'No'
    price REAL,
    amount REAL,
    shares REAL,
    fee REAL,
    FOREIGN KEY (condition_id) REFERENCES markets(condition_id)
);

-- Tabla de precios crypto
CREATE TABLE crypto_prices (
    date DATE,
    crypto TEXT,
    open REAL,
    high REAL,
    low REAL,
    close REAL,
    volume REAL,
    PRIMARY KEY (date, crypto)
);

-- Índices para rendimiento
CREATE INDEX idx_trades_condition ON trades(condition_id);
CREATE INDEX idx_trades_price ON trades(price);
CREATE INDEX idx_trades_time ON trades(block_time);
CREATE INDEX idx_markets_crypto ON markets(crypto);
CREATE INDEX idx_markets_date ON markets(resolution_date);
```

---

## 5. LÓGICA DEL BACKTEST DETALLADA

### 5.1 Fill Simulator: ¿Se habría llenado tu orden?

**Esto es CRÍTICO. La lógica:**

```python
def can_fill_order(order_price, order_side, market_trades, order_time):
    """
    Determina si una orden límite se habría llenado.
    
    Para que tu orden se llene a price X:
    1. Debe haber trades al precio X o mejor DESPUÉS de tu order_time
    2. Debe haber suficiente volumen al nivel X
    
    Modelos de fill:
    
    OPTIMISTA: Si hubo CUALQUIER trade a precio <= X (para compra), fill = True
    
    REALISTA: 
    - Filtrar trades después de order_time al precio <= X
    - Sumar volumen disponible a ese nivel
    - Si volumen_disponible >= tu orden → fill (parcial posible)
    - Aplicar queue_multiplier (competencia de otras órdenes)
    
    CONSERVADOR:
    - Solo fill si el precio promedio ponderado por volumen 
      a ese nivel fue <= X durante al menos 5 minutos
    """
```

### 5.2 Lógica de cada estrategia

#### Estrategia A: Stink Bid

```python
def backtest_stink_bid(market, crypto_price, config):
    """
    Para cada mercado resuelto:
    
    1. IDENTIFICAR el mercado:
       - Parsear question para extraer: crypto, threshold_price, resolution_date
       - Ej: "Will BTC be above $74,000 on Mar 3?" 
         → crypto=BTC, threshold=74000, date=2026-03-03
    
    2. CALCULAR distancia del precio actual:
       - btc_price_at_market_open = crypto_prices[market.first_trade_time]
       - distance_pct = abs(threshold - btc_price) / btc_price * 100
       
    3. DETERMINAR qué apostar:
       - Si threshold > btc_price (mercado de subida):
         → Comprar YES a config.tier1_price
       - Si threshold < btc_price (mercado de bajada):
         → Comprar NO a config.tier1_price
    
    4. SIMULAR FILL:
       - Buscar si hubo trades al precio tier1_price o mejor
       - Si hay → fill. Si no → miss (no hay ganancia ni pérdida).
    
    5. CALCULAR P&L:
       - Si filled Y el mercado resolvió a nuestro favor:
         → profit = (shares * $1) - capital - fees
       - Si filled Y el mercado resolvió EN CONTRA:
         → loss = -capital - fees  (shares valen $0)
       - Si NOT filled:
         → P&L = 0 (la orden no se ejecutó, no pierdes nada)
       
       IMPORTANTE: En Polymarket no pagas si tu orden no se llena.
       Solo pierdes si la orden se llena Y el mercado resuelve en contra.
    
    6. EXIT ANTICIPADO (si config.exit == "sell_at_target"):
       - Buscar si después del fill, el precio subió a target
       - Si lo encuentra → profit = shares * (sell_price - buy_price) - fees
    """
```

#### Estrategia B: Volatility Spread

```python
def backtest_volatility_spread(market_group, crypto_price, config):
    """
    Para cada FECHA de resolución:
    
    1. ENCONTRAR todos los mercados para esa fecha:
       - Ej: para BTC Mar 3: markets at $62K, $64K, $66K... $86K, $88K
    
    2. CALCULAR spread levels:
       - btc_price = $80,000
       - upper_market = closest market above btc_price + spread_pct%
       - lower_market = closest market below btc_price - spread_pct%
    
    3. COLOCAR ÓRDENES:
       - Comprar YES en upper_market (apuesta a subida)
       - Comprar NO en lower_market (apuesta a bajada)  
       Ambos a config.entry_price_max o mejor
    
    4. SIMULAR FILLS Y P&L:
       - Igual que Estrategia A pero para ambos lados
       - P&L total = P&L_upper + P&L_lower
    """
```

### 5.3 Métricas a calcular por cada backtest run

```python
METRICS = {
    # P&L
    'total_pnl': float,              # P&L neto total
    'total_invested': float,          # Capital total desplegado
    'total_return_pct': float,        # ROI
    'avg_pnl_per_market': float,
    
    # Win/Loss
    'total_markets': int,             # Mercados analizados
    'orders_placed': int,             # Órdenes que intentamos colocar
    'orders_filled': int,             # Órdenes que se llenaron
    'fill_rate': float,               # orders_filled / orders_placed
    'wins': int,                      # Fills que resultaron en ganancia
    'losses': int,                    # Fills que resultaron en pérdida
    'win_rate': float,                # wins / orders_filled
    'missed': int,                    # Órdenes no ejecutadas
    
    # Risk
    'avg_win': float,
    'avg_loss': float,
    'profit_factor': float,           # gross_wins / gross_losses
    'max_drawdown': float,
    'max_consecutive_losses': int,
    'expectancy': float,              # (WR * avg_win) - (LR * avg_loss)
    
    # Ratios
    'sharpe_ratio': float,
    'sortino_ratio': float,
    'calmar_ratio': float,
    
    # Granular
    'pnl_by_crypto': dict,            # {'BTC': x, 'ETH': y, 'SOL': z}
    'pnl_by_month': dict,
    'pnl_by_distance_pct': dict,      # P&L por rango de distancia
    'fill_rate_by_price': dict,       # Fill rate por nivel de precio
    'win_rate_by_crypto': dict,
    'best_trade': float,
    'worst_trade': float,
}
```

---

## 6. GRID SEARCH: BACKTESTS PARALELOS

### 6.1 Parámetros a barrer (Estrategia A)

```python
GRID_STINK_BID = {
    'tier1_price': [0.01, 0.02, 0.03, 0.04, 0.05, 0.07, 0.10],
    'tier2_price': [0.001, 0.002, 0.003, 0.005, 0.01],
    'tier1_capital': [25, 50, 100],
    'tier2_capital': [1, 3, 5],
    'exit_strategy': ['hold_to_resolution', 'sell_at_2x', 'sell_at_5x', 'sell_at_10x'],
    'crypto': ['BTC', 'ETH', 'SOL', 'ALL'],
    'fill_model': ['optimistic', 'realistic'],
}
# Total combinaciones: 7 * 5 * 3 * 3 * 4 * 4 * 2 = 10,080 backtests
```

### 6.2 Parámetros a barrer (Estrategia B)

```python
GRID_VOLATILITY = {
    'spread_percent': [3, 5, 7, 10, 15],
    'entry_price_max': [0.03, 0.05, 0.07, 0.10],
    'capital_per_side': [50, 100, 200],
    'exit_strategy': ['hold_to_resolution', 'sell_at_target'],
    'sell_target_pct': [50, 100, 200],  # Solo si exit=sell_at_target
    'crypto': ['BTC', 'ETH', 'SOL', 'ALL'],
    'fill_model': ['optimistic', 'realistic'],
}
# Total: 5 * 4 * 3 * 2 * 3 * 4 * 2 = 2,880 backtests
```

### 6.3 Grid Runner para VPS

```python
# run_vps.py - Script para dejar corriendo en VPS
"""
1. Carga datos de SQLite
2. Genera todas las combinaciones de parámetros
3. Ejecuta cada backtest (usando multiprocessing para paralelizar)
4. Guarda resultados incrementalmente en CSV
5. Al finalizar, genera:
   - master_results.csv: una fila por backtest con todas las métricas
   - best_params.csv: top 20 configuraciones por ROI, Sharpe, etc.
   - trades_detail_{strategy}_{params}.csv: detalle trade-by-trade
   - summary_report.html: dashboard interactivo
   - heatmaps/*.png: heatmaps de sensibilidad
"""

import multiprocessing as mp
from itertools import product
import logging

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    handlers=[
        logging.FileHandler('arbibot.log'),
        logging.StreamHandler()
    ]
)

def run_single_backtest(params_dict) -> dict:
    """Ejecuta un backtest con parámetros dados. Returns metrics dict."""
    ...

def main():
    # 1. Load data
    data = load_cached_data()
    
    # 2. Generate all parameter combinations
    all_params = generate_grid(GRID_STINK_BID) + generate_grid(GRID_VOLATILITY)
    logging.info(f"Total backtests to run: {len(all_params)}")
    
    # 3. Run in parallel
    with mp.Pool(processes=mp.cpu_count()) as pool:
        results = []
        for i, result in enumerate(pool.imap_unordered(run_single_backtest, all_params)):
            results.append(result)
            if i % 100 == 0:
                logging.info(f"Progress: {i}/{len(all_params)} ({i/len(all_params)*100:.1f}%)")
                # Save incrementally
                save_incremental(results)
    
    # 4. Final export
    export_all_results(results)
    generate_report(results)
    logging.info("DONE - All backtests complete")

if __name__ == '__main__':
    main()
```

---

## 7. OUTPUTS: QUÉ ARCHIVOS SE GENERAN

### 7.1 CSVs

```
output/csv/
├── master_results.csv          # Una fila por backtest run
│   Columnas: strategy, crypto, tier1_price, tier2_price, tier1_capital,
│   tier2_capital, exit_strategy, fill_model, total_pnl, roi, win_rate,
│   fill_rate, sharpe, max_dd, profit_factor, num_trades, num_wins, ...
│
├── best_stinkbid_params.csv    # Top 50 configs Estrategia A
├── best_volatility_params.csv  # Top 50 configs Estrategia B
│
├── trades_stinkbid_detail.csv  # TODOS los trades de la mejor config
│   Columnas: market_id, question, crypto, threshold_price, distance_pct,
│   entry_price, shares, fill_time, exit_reason, exit_price, pnl, 
│   resolution_outcome, resolution_date, ...
│
├── trades_volatility_detail.csv
│
├── fill_rate_analysis.csv      # Fill rate por precio y crypto
│   Columnas: crypto, price_level, total_orders, filled_orders, fill_rate
│
├── monthly_pnl.csv             # P&L mensual por estrategia y crypto
│   Columnas: month, strategy, crypto, pnl, roi, num_trades, win_rate
│
└── risk_analysis.csv           # Drawdowns, worst streaks, etc.
```

### 7.2 Spreadsheets (XLSX)

```
output/xlsx/
├── arbibot_analysis.xlsx       # Workbook completo con:
│   ├── Sheet "Master Results"  # Todos los backtests
│   ├── Sheet "Best Params"     # Mejores configuraciones
│   ├── Sheet "Monthly P&L"     # P&L mensual  
│   ├── Sheet "Trade Log"       # Trades detallados
│   ├── Sheet "Fill Rate"       # Análisis de fill rate
│   ├── Sheet "Risk Metrics"    # Drawdowns, Sharpe, etc.
│   └── Sheet "Comparison"      # Side-by-side estrategias
│
└── sensitivity_analysis.xlsx   # Grid search results en formato pivot
    ├── Sheet "StinkBid Heatmap" # tier1_price vs tier1_capital → ROI
    └── Sheet "Volatility Heatmap" # spread% vs entry_price → ROI
```

### 7.3 HTML Report

```
output/reports/
├── dashboard.html              # Dashboard interactivo standalone
│   Incluye:
│   - Equity curves (P&L acumulado en el tiempo)
│   - Tabla de mejores configuraciones
│   - Heatmaps de sensibilidad (matplotlib embedded)
│   - Distribución de P&L por trade
│   - Fill rate por nivel de precio
│   - Monthly returns heatmap
│   - Comparison BTC vs ETH vs SOL
│   - Scatter plot: Win Rate vs ROI por config
│   - Worst case scenarios (max drawdown analysis)
│
└── charts/                     # PNGs individuales
    ├── equity_curve_stinkbid.png
    ├── equity_curve_volatility.png
    ├── heatmap_stinkbid_price_vs_capital.png
    ├── heatmap_volatility_spread_vs_entry.png
    ├── fill_rate_by_price.png
    ├── pnl_distribution.png
    └── monthly_returns.png
```

---

## 8. CONFIGURACIÓN YAML COMPLETA

```yaml
# config/config.yaml

# === DATA SOURCE ===
data:
  dune_api_key_env: "DUNE_API_KEY"  # Lee del environment variable
  db_path: "data/arbibot.db"
  
  # Rango de datos a descargar
  start_date: "2024-06-01"
  end_date: null  # null = hasta hoy
  
  # Cryptos a analizar
  cryptos: ["BTC", "ETH", "SOL"]
  
  # Filtros de calidad de datos
  min_trades_per_market: 10
  min_volume_per_market: 100  # USD

# === FEES ===
fees:
  trading_fee_pct: 2.0      # % sobre el monto
  resolution_fee_pct: 0.0   # Sin fee en resolución

# === FILL SIMULATION ===
simulation:
  fill_models: ["optimistic", "realistic"]
  
  optimistic:
    # Fill si CUALQUIER trade existió al precio objetivo o mejor
    description: "Fill if any historical trade at target price"
  
  realistic:
    # Fill solo si hubo suficiente volumen al nivel
    queue_multiplier: 3.0    # Asume 3x competencia
    min_volume_at_level: 10  # USD mínimo de volumen al nivel
    slippage_bps: 50         # 0.5% de slippage

# === STRATEGIES ===
strategies:
  stink_bid:
    enabled: true
    grid:
      tier1_price: [0.01, 0.02, 0.03, 0.04, 0.05, 0.07, 0.10]
      tier2_price: [0.001, 0.002, 0.003, 0.005, 0.01]
      tier1_capital: [25, 50, 100]
      tier2_capital: [1, 3, 5]
      exit_strategy: ["hold_to_resolution", "sell_at_2x", "sell_at_5x", "sell_at_10x"]
  
  volatility_spread:
    enabled: true
    grid:
      spread_percent: [3, 5, 7, 10, 15]
      entry_price_max: [0.03, 0.05, 0.07, 0.10]
      capital_per_side: [50, 100, 200]
      exit_strategy: ["hold_to_resolution", "sell_at_target"]
      sell_target_pct: [50, 100, 200]

# === VPS EXECUTION ===
execution:
  parallel_workers: 4        # Número de procesos paralelos
  save_interval: 100         # Guardar cada N backtests
  log_level: "INFO"
  
# === OUTPUT ===
output:
  dir: "output/"
  csv: true
  xlsx: true
  html_report: true
  charts: true
  top_n_results: 50          # Top N configs a guardar en detalle
```

---

## 9. PLAN DE EJECUCIÓN POR SESIONES

### Sesión 1: Data Pipeline (PRIMERA PRIORIDAD)

**Objetivo:** Descargar todos los datos y tenerlos en SQLite local.

**Archivos:**
1. `requirements.txt`
2. `config/config.yaml`
3. `models/market.py`, `models/trade.py`, `models/config_models.py`
4. `data/dune_client.py` — cliente API Dune con rate limiting y paginación
5. `data/price_fetcher.py` — descarga precios BTC/ETH/SOL de CoinGecko
6. `data/market_resolver.py` — parsea questions, extrae threshold, cruza con resoluciones
7. `data/data_manager.py` — orquesta todo: descarga → parsea → SQLite

**Test de validación:**
```bash
python -m data.data_manager --fetch --validate
# Debe mostrar: "Downloaded X markets, Y trades. Data validated OK."
```

**NOTA IMPORTANTE:** La query de Dune puede devolver millones de filas. Usar la API de Dune con paginación (`limit` + `offset`) y descargar por lotes. Guardar el `execution_id` de Dune y usar la endpoint de resultados. Considerar particionar la query por mes si es demasiado grande.

---

### Sesión 2: Backtest Engine Core

**Archivos:**
1. `backtest/fee_calculator.py`
2. `backtest/fill_simulator.py` — los dos modelos (optimistic + realistic)
3. `backtest/strategy_base.py` — ABC con interfaces claras
4. `backtest/strategies/stink_bid.py`
5. `backtest/strategies/volatility_spread.py`
6. `backtest/engine.py` — ejecuta un backtest con params dados

**Test:**
```bash
python -m backtest.engine --strategy stink_bid --crypto BTC \
  --tier1-price 0.03 --tier1-capital 50 --exit hold_to_resolution
# Debe mostrar resultados de un solo backtest
```

---

### Sesión 3: Grid Search + VPS Runner

**Archivos:**
1. `backtest/grid_runner.py` — genera combinaciones, ejecuta en paralelo
2. `run_vps.py` — script principal para VPS
3. `analysis/metrics.py` — cálculo de todas las métricas
4. `analysis/optimizer.py` — ranking de resultados, best params

**Test:**
```bash
python run_vps.py --quick  # Corre subset pequeño para validar
# Debe generar output/csv/master_results.csv
```

---

### Sesión 4: Export + Reporting

**Archivos:**
1. `analysis/export.py` — genera CSVs y XLSX
2. `analysis/report.py` — genera HTML dashboard
3. `templates/report.html` — template Jinja2
4. Ampliar `main.py` con todos los comandos CLI

**Test:**
```bash
python main.py report --input output/csv/master_results.csv
# Debe generar output/reports/dashboard.html y output/xlsx/arbibot_analysis.xlsx
```

---

### Sesión 5: Run Completo + Análisis

1. Ejecutar grid search completo en VPS
2. Analizar resultados
3. Identificar mejores parámetros
4. Re-run con configuraciones óptimas para detalle trade-by-trade
5. Decidir si pasar a live

---

## 10. INSTRUCCIONES ESPECÍFICAS PARA SONNET

### Principios de código:
- **Python 3.10+**, type hints en todo, dataclasses (no dicts)
- **Logging** estándar, no prints
- **pandas** para datos, pero interfaces limpias con dataclasses
- **Click** para CLI
- **multiprocessing** para parallelismo (no threading, no async)
- **SQLite** para cache, con índices bien diseñados
- **matplotlib** para gráficos, embebidos como base64 en HTML
- **openpyxl** para XLSX
- **Jinja2** para HTML reports
- **PyYAML** para configuración

### Parsing de mercados (CRITICAL):
El campo `question` tiene formatos variados. Necesitas un parser robusto:
```python
# Patrones encontrados en los datos reales:
"Will the price of Bitcoin be above $74,000 on March 3?"
"Will Ethereum hit $5,000 by December 31?"
"Will the price of Bitcoin be above $80,000 on February 5?"

# El parser debe extraer:
# - crypto: BTC | ETH | SOL
# - threshold: float (74000, 5000, 80000...)
# - direction: "above" | "below" | "hit"
# - resolution_date: date
# - period_type: "on_date" | "by_date"
```

### Manejo de datos Dune (CRITICAL):
- La API de Dune devuelve máximo 250K filas por ejecución
- Para datasets grandes, usar el SDK de Dune (`dune-client` pip package) 
- O particionar queries por rango de fechas
- Siempre incluir `evt_block_date` o `block_time` filters para partition pruning
- Cachear en SQLite agresivamente — no re-queryear datos ya descargados

### Fill simulation (CRITICAL):
- **No asumir que toda orden se llena.** Verificar contra trades históricos.
- En modo `optimistic`: fill si hubo cualquier trade al precio o mejor
- En modo `realistic`: fill solo si volumen disponible >= order_size * queue_multiplier
- **Las órdenes no ejecutadas tienen P&L = 0**, no pérdida. Solo pierdes capital en órdenes que SÍ se llenan y el mercado resuelve en contra.

### Exit anticipado:
- Para `sell_at_Nx`: buscar en los trades posteriores si el precio del token subió a N veces el precio de compra
- Si encontramos ese precio → exit con ganancia
- Si no lo encontramos → mantener hasta resolución

### Spreadsheet output:
- Usar openpyxl con formato condicional (verde/rojo para P&L)
- Headers congelados
- Auto-width de columnas
- Una hoja por cada tipo de análisis
- Incluir gráficos en el XLSX donde sea posible

---

## 11. DEPENDENCIAS

```
# requirements.txt
pandas>=2.0
numpy>=1.24
matplotlib>=3.7
seaborn>=0.12
openpyxl>=3.1
click>=8.1
pyyaml>=6.0
jinja2>=3.1
requests>=2.31
dune-client>=1.7
python-dateutil>=2.8
tqdm>=4.65
```

---

## 12. VARIABLES DE ENTORNO NECESARIAS

```bash
# .env (NO commitear)
DUNE_API_KEY=your_dune_api_key_here
```

---

## 13. CHECKLIST FINAL

Antes de ejecutar en VPS:

- [ ] SQLite tiene datos de ≥6 meses de mercados BTC/ETH/SOL
- [ ] Parser de questions funciona con TODOS los formatos encontrados
- [ ] Precios históricos crypto descargados y cruzados con mercados
- [ ] Resoluciones cruzadas correctamente con condition_id
- [ ] Fill simulator probado: optimistic siempre da más fills que realistic
- [ ] Un backtest individual completa en <1 segundo
- [ ] Grid search de 100 combos completa en <2 minutos
- [ ] CSVs se generan correctamente con todas las columnas
- [ ] XLSX se abre en Excel/Google Sheets sin errores
- [ ] HTML report se visualiza correctamente en browser
- [ ] Logs se escriben a archivo
- [ ] Script VPS se puede dejar corriendo con `nohup`
