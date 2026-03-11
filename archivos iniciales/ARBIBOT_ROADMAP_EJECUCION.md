# ARBIBOT: ROADMAP DE EJECUCIÓN Y MEJORA CONTINUA
## Del backtest al live trading y más allá

---

## FASE 0: CONSTRUCCIÓN (Semana 1)

### Sesiones con Sonnet
- Sesión 1-2: Data pipeline (Dune → SQLite → datos limpios)
- Sesión 3: Backtest engine + estrategias
- Sesión 4: Grid runner + VPS script
- Sesión 5: Export (CSV/XLSX) + HTML report

### Entregable
- Sistema completo corriendo en VPS
- Grid search de ~13,000 combinaciones ejecutado
- Primeros resultados en CSV/XLSX para analizar

---

## FASE 1: ANÁLISIS Y VALIDACIÓN (Semana 2)

### 1.1 Analizar resultados del grid search

Abrir `master_results.csv` y ordenar por:
- Total P&L (¿quién gana más?)
- Sharpe Ratio (¿quién gana más por unidad de riesgo?)
- Win Rate (¿quién acierta más?)
- Profit Factor (¿cuánto ganas por cada $ que pierdes?)

Buscar configuraciones que estén en el TOP 20 en TODAS estas métricas,
no solo en una. Una config con ROI alto pero Sharpe bajo es peligrosa.

### 1.2 Preguntas que DEBEN responderse antes de avanzar

```
□ ¿Las top 5 configs son rentables tanto en modelo optimista como realista?
□ ¿La rentabilidad es consistente mes a mes (no concentrada en 1-2 meses)?
□ ¿Funciona en al menos 2 de los 3 cryptos?
□ ¿El max drawdown es soportable? (si es -$500 con capital de $106, ¿puedes aguantar 5 semanas perdiendo seguido?)
□ ¿El fill rate del modelo realista es >5%? (si es <5%, muy pocas órdenes se llenan)
□ ¿Hay suficientes trades (>30) para que los resultados sean estadísticamente significativos?
□ ¿La mejor config de Estrategia A es mejor que la mejor de Estrategia B, o viceversa?
□ ¿Combinar ambas estrategias mejora el Sharpe vs cada una sola?
```

### 1.3 Sensitivity analysis

De los heatmaps generados, identificar:
- ¿El P&L es muy sensible al precio de entrada? (ej: 3¢ vs 4¢ cambia todo)
  → Si sí: el edge es frágil, cuidado
  → Si no: el edge es robusto, bien
  
- ¿El capital óptimo es estable?
  → Si $50 y $100 dan resultados similares, el sistema escala
  → Si solo funciona con exactamente $50, hay algo raro

### 1.4 Validación out-of-sample

CRÍTICO: Reservar los últimos 30 días de datos SIN usarlos en el grid search.
Después correr las top 5 configuraciones SOLO en esos 30 días.

```
Si OOS_return >= 50% de in-sample_return → resultado válido, proceder
Si OOS_return >= 25% de in-sample_return → resultado aceptable con cautela  
Si OOS_return < 25% de in-sample_return → probable overfitting, NO proceder
```

---

## FASE 2: PAPER TRADING (Semana 3)

### 2.1 Qué hacer

Con las mejores 2-3 configuraciones validadas:

1. Cada día, a la hora de apertura de los mercados:
   - Anotar qué órdenes colocarías (crypto, mercado, precio, capital)
   - Verificar en Polymarket si esas órdenes se habrían llenado
   - Registrar el resultado real del mercado
   
2. Llevar un spreadsheet manual:
   
   | Fecha | Crypto | Mercado | Lado | Precio Orden | ¿Llenada? | Resolución | P&L Teórico |
   
3. Al final de la semana comparar:
   - P&L paper vs P&L que predice el backtest
   - Fill rate real vs fill rate del backtest

### 2.2 Qué buscar

- Si el fill rate real es MUCHO menor que el del backtest → 
  el modelo de fill es demasiado optimista, necesitas ajustar
  
- Si los precios disponibles son peores que los del backtest → 
  hay más competencia de la que el modelo asume

- Si los resultados coinciden razonablemente → luz verde para live

---

## FASE 3: LIVE TRADING GRADUAL (Semana 4-5)

### 3.1 Capital inicial: REDUCIDO

```
Config del backtest dice: $106/día (4 apuestas)
Empezar con: $25-50/día (2 apuestas, las de mayor edge)
```

Razón: verificar que la ejecución real funciona antes de escalar.

### 3.2 Automatización

En esta fase NO automatizar la ejecución de órdenes.
Colocar órdenes manualmente en Polymarket.
Razón: necesitas sentir el mercado, ver el order book real, entender los tiempos.

### 3.3 Logging

Registrar CADA orden en un spreadsheet/CSV:
- Timestamp exacto de colocación
- Precio pedido vs precio ejecutado
- Tiempo hasta fill (o si no se llenó)
- Slippage real
- Resolución del mercado
- P&L real vs P&L esperado por backtest

### 3.4 Reglas de stop

```
PARAR si:
- Pierdes 3x el drawdown máximo del backtest
- Fill rate real es <50% del fill rate del backtest
- 3 semanas seguidas con P&L negativo
- Descubres un fee o costo que el backtest no incluía

ESCALAR si:
- 2 semanas consecutivas con P&L positivo
- Fill rate real está dentro del ±30% del backtest
- Drawdown real < drawdown máximo del backtest
```

---

## FASE 4: ESCALADO (Semana 6-8)

### 4.1 Aumentar capital gradualmente

```
Semana 6: 50% del capital objetivo
Semana 7: 75% del capital objetivo  
Semana 8: 100% del capital objetivo
```

### 4.2 Expandir a más mercados

Si BTC funciona bien, añadir ETH.
Si ETH también funciona, añadir SOL.
No añadir los tres a la vez.

### 4.3 Automatización parcial

Una vez confirmado que el sistema funciona:

1. **Script de alertas**: monitorea Polymarket y te avisa por Telegram 
   cuando hay un mercado nuevo que cumple tus criterios
   
2. **Script de tracking**: registra automáticamente tus órdenes y 
   calcula P&L en tiempo real
   
3. **NO automatizar la colocación de órdenes todavía** — 
   demasiado riesgo si hay un bug

---

## FASE 5: MEJORA CONTINUA (Mes 2+)

### 5.1 Re-backtest mensual

Cada mes:
1. Descargar datos nuevos de Dune
2. Re-correr el grid search con datos actualizados
3. Comparar: ¿los mejores params cambiaron?
   - Si son estables → el edge es real
   - Si cambian mucho → el edge es temporal, adaptar

### 5.2 Nuevas ideas a investigar

Con los datos de Dune ya descargados, puedes explorar:

a) **Timing optimization**: ¿Comprar en apertura del mercado o esperar 
   2-3 horas a que baje el precio?
   
b) **Volatility regime filter**: No operar en semanas de baja volatilidad.
   Usar ATR (Average True Range) de 7 días para filtrar.
   
c) **Event-driven**: ¿Los mercados dan mejores resultados en semanas con 
   FOMC, CPI, o earnings de grandes empresas? ETH es especialmente 
   sensible a eventos macro según los papers académicos.

d) **Correlation entre mercados**: Si BTC se mueve, ¿ETH y SOL lo siguen?
   ¿Puedes usar el movimiento de BTC como señal para apostar en ETH/SOL?

e) **Market maker detection**: Identificar si hay market makers consistentes 
   en los niveles de stink bid. Si los hay, puedes anticipar su comportamiento.

f) **Otros mercados Polymarket**: No limitarse a crypto. Mercados de política, 
   deportes, y eventos también tienen tail risk mal priced.
   
g) **Dynamic sizing**: Ajustar el capital por apuesta según la volatilidad 
   implícita del mercado. Más capital cuando la vol es alta (más prob de hit).

### 5.3 Automatización completa (Mes 3+)

Solo cuando tengas 2+ meses de datos live que confirmen el edge:

1. **Polymarket API integration**: colocar órdenes automáticamente
2. **Risk manager**: límites diarios/semanales de pérdida
3. **Dashboard en tiempo real**: P&L live, órdenes activas, alertas
4. **Auto-shutdown**: si el drawdown excede un umbral, para todo

### 5.4 Diversificación

Con el framework de ArbiBot ya construido, añadir:

- Nuevos cryptos cuando Polymarket los añada
- Mercados semanales vs mensuales (diferentes timeframes)
- Mercados de otros tipos (política, deportes) adaptando la estrategia
- Otros prediction markets (Kalshi, Limitless) si tienen API

---

## MÉTRICAS CLAVE A MONITOREAR SIEMPRE

### Diarias
- P&L del día
- Órdenes colocadas vs llenadas
- Desviación vs backtest

### Semanales
- P&L semanal, win rate, fill rate
- Comparación con predicción del backtest
- ¿Algún mercado/crypto underperformando?

### Mensuales
- ROI mensual
- Sharpe rolling de 30 días
- Max drawdown rolling
- ¿Los parámetros óptimos cambiaron?
- ¿Necesito re-calibrar?

---

## RESUMEN DE TIMELINE

```
Semana 1     → Construir con Sonnet
Semana 2     → Analizar backtests, validar out-of-sample
Semana 3     → Paper trading manual
Semana 4-5   → Live con capital reducido (25-50%)
Semana 6-8   → Escalar a capital completo si los resultados confirman
Mes 2+       → Mejora continua, nuevas estrategias, re-calibración
Mes 3+       → Automatización completa (si todo va bien)
```

Total desde hoy hasta live con capital completo: ~8 semanas
Pero el primer dinero real entra en semana 4 (con capital reducido).

---

## INVERSIÓN NECESARIA

### Infraestructura
- VPS: ~$5-20/mes (cualquier VPS básico, 2GB RAM suficiente)
- Dune API: tienes API key, el plan gratuito permite ~2,500 queries/mes
- Polymarket: capital de trading (el que tú definas)

### Tiempo
- Construcción: ~10-15 horas con Sonnet
- Análisis semanal: ~2-3 horas
- Operativa diaria (manual): ~15-30 min si no automatizas
- Operativa diaria (automática): ~5 min de supervisión

### Capital mínimo recomendado para empezar
- Fase paper: $0
- Fase live reducido: $200-500 (para 1-2 semanas a ~$25-50/día)
- Fase live completo: depende de tus configs óptimas, pero ~$500-2000

---

## RIESGOS A GESTIONAR

1. **Riesgo de modelo**: El backtest sobreestima el edge → paper trading lo detecta
2. **Riesgo de liquidez**: No hay sellers a tu precio → ajustar precios de entrada
3. **Riesgo de plataforma**: Polymarket cambia fees o reglas → monitorear
4. **Riesgo de mercado**: Período prolongado de baja vol → el stop loss te saca
5. **Riesgo regulatorio**: Polymarket restringido en tu jurisdicción → verificar antes
6. **Riesgo técnico**: Bug en el sistema automático → por eso manual primero
