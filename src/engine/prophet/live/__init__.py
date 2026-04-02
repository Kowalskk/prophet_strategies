"""
Live trading module — parallel to paper trading, never mixed.

All live state lives in the ``live_positions`` and ``live_orders`` DB tables.
Paper trading tables (positions, paper_orders) are never touched from here.

Enable by setting PAPER_TRADING=false in .env and LIVE_STRATEGIES=srb_cheap_x5,...
"""
