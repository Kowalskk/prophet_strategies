"""
PROPHET STRATEGIES
Price fetcher — downloads historical BTC/ETH/SOL daily prices from CoinGecko
"""
from __future__ import annotations
import logging
import time
from datetime import date, datetime, timedelta
from typing import Optional
import requests

logger = logging.getLogger(__name__)

COINGECKO_BASE = "https://api.coingecko.com/api/v3"
COINGECKO_IDS = {
    "BTC": "bitcoin",
    "ETH": "ethereum",
    "SOL": "solana",
}


class PriceFetcher:
    """Downloads and caches historical OHLC prices for crypto assets."""

    def __init__(self):
        self.session = requests.Session()
        self.session.headers.update({"Accept": "application/json"})
        self._cache: dict[str, dict[date, float]] = {}  # {crypto: {date: close_price}}

    def fetch_daily_prices(
        self,
        crypto: str,
        start_date: date,
        end_date: date,
    ) -> dict[date, float]:
        """
        Fetch daily close prices for a crypto asset.
        Returns dict of {date: close_price_usd}.
        """
        if crypto in self._cache:
            logger.debug(f"Using cached prices for {crypto}")
            return self._cache[crypto]

        coin_id = COINGECKO_IDS.get(crypto)
        if not coin_id:
            raise ValueError(f"Unknown crypto: {crypto}. Supported: {list(COINGECKO_IDS.keys())}")

        logger.info(f"Fetching {crypto} prices from CoinGecko ({start_date} to {end_date})...")

        # CoinGecko market_chart/range endpoint
        from_ts = int(datetime.combine(start_date, datetime.min.time()).timestamp())
        to_ts = int(datetime.combine(end_date, datetime.min.time()).timestamp())

        url = f"{COINGECKO_BASE}/coins/{coin_id}/market_chart/range"
        params = {"vs_currency": "usd", "from": from_ts, "to": to_ts}

        try:
            resp = self.session.get(url, params=params, timeout=30)
            resp.raise_for_status()
            data = resp.json()
        except requests.RequestException as e:
            logger.warning(f"CoinGecko fetch failed for {crypto}: {e}. Using fallback.")
            return self._fallback_prices(crypto, start_date, end_date)

        prices: dict[date, float] = {}
        for ts_ms, price in data.get("prices", []):
            d = datetime.utcfromtimestamp(ts_ms / 1000).date()
            prices[d] = price

        logger.info(f"Fetched {len(prices)} price points for {crypto}")
        self._cache[crypto] = prices
        time.sleep(1.0)  # CoinGecko rate limit
        return prices

    def get_price_on_date(
        self,
        crypto: str,
        target_date: date,
        start_date: Optional[date] = None,
        end_date: Optional[date] = None,
    ) -> Optional[float]:
        """Get the price of crypto on a specific date."""
        if start_date is None:
            start_date = target_date - timedelta(days=30)
        if end_date is None:
            end_date = target_date + timedelta(days=7)

        prices = self.fetch_daily_prices(crypto, start_date, end_date)

        # Exact match first
        if target_date in prices:
            return prices[target_date]

        # Search nearby (±3 days)
        for delta in range(1, 4):
            for sign in [1, -1]:
                d = target_date + timedelta(days=delta * sign)
                if d in prices:
                    logger.debug(f"No price for {crypto} on {target_date}, using {d} (±{delta}d)")
                    return prices[d]

        return None

    def _fallback_prices(
        self,
        crypto: str,
        start_date: date,
        end_date: date,
    ) -> dict[date, float]:
        """
        Fallback: approximate prices if CoinGecko is unavailable.
        Uses rough estimates based on known historical ranges.
        Only used if API is down — will be replaced with real data.
        """
        logger.warning(f"Using FALLBACK prices for {crypto} — not suitable for production!")
        
        # Very rough approximate prices (mid-2024 to early 2025)
        base_prices = {
            "BTC": 65000.0,
            "ETH": 3200.0,
            "SOL": 160.0,
        }
        base = base_prices.get(crypto, 1000.0)
        
        prices = {}
        current = start_date
        while current <= end_date:
            # Add slight variation
            import random
            variation = 1 + random.uniform(-0.05, 0.05)
            prices[current] = base * variation
            current += timedelta(days=1)
        
        return prices
