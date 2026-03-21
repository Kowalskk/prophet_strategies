# Source Code

Main application code organized by component.

## Components

### `engine/` - Backend
FastAPI application handling:
- Market data collection
- Position management
- Trading logic
- API endpoints
- Database operations

**Key Files:**
- `app.py` - Application entry point
- `api/` - REST API routes
- `models/` - Database/Pydantic models
- `services/` - Business logic

**Run:**
```bash
cd src/engine
python -m uvicorn app:app --reload
```

### `dashboard/` - Frontend
Next.js application for:
- Portfolio tracking
- Position visualization
- Market monitoring
- Real-time updates

**Key Files:**
- `app/` - App Router pages
- `components/` - React components
- `lib/` - Utilities and API client
- `public/` - Static assets

**Run:**
```bash
cd src/dashboard
npm install
npm run dev
```

### `backtest/` - Backtesting Engine
Strategy backtesting and optimization:
- `engine.py` - Core backtest engine
- `strategy_base.py` - Base strategy class
- `strategies/` - Concrete strategy implementations
- Grid search optimization

### `analysis/` - Analytics & Metrics
Data analysis and reporting:
- `metrics.py` - Performance metrics
- `optimizer.py` - Strategy optimization
- `export.py` - Data export
- `report.py` - Report generation

### `models/` - Data Models
Shared data structures for:
- Database schemas
- API request/response types
- Configuration classes

### `config/` - Configuration
Application configuration:
- Strategy parameters
- Engine settings
- API configuration

### `templates/` - HTML Templates
Email templates and static HTML files.

## Architecture

```
src/
├── engine/              # FastAPI backend
├── dashboard/           # Next.js frontend
├── backtest/           # Backtesting engine
├── analysis/           # Analytics
├── models/             # Data models
├── config/             # Configuration
└── templates/          # Templates
```

## Development

1. **Backend changes**: Edit files in `engine/`, restart FastAPI
2. **Frontend changes**: Edit files in `dashboard/`, Next.js hot-reload
3. **Strategy changes**: Edit `backtest/strategies/`, rerun backtest
4. **Models**: Keep `models/` DRY - shared across components

## Testing

```bash
# Backend tests
cd src/engine
pytest

# Frontend tests
cd src/dashboard
npm test
```
