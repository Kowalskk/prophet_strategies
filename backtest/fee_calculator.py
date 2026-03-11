"""
PROPHET STRATEGIES
Fee calculator — Polymarket fee structure
"""
from __future__ import annotations


class FeeCalculator:
    """
    Polymarket charges a 2% fee on the trade amount (not on shares).
    No fee on resolution payout.

    Example:
        Buy $50 worth of YES tokens at 3¢ each:
        - Amount paid: $50
        - Fee: $50 * 2% = $1.00
        - Total cost: $51.00
        - Shares received: 50 / 0.03 ≈ 1,666 shares
        - If YES resolves: 1,666 * $1.00 = $1,666 gross payout
        - Net P&L: $1,666 - $51 = $1,615
    """

    def __init__(self, trading_fee_pct: float = 2.0, resolution_fee_pct: float = 0.0):
        self.trading_fee_pct = trading_fee_pct
        self.resolution_fee_pct = resolution_fee_pct

    def trading_fee(self, amount_usd: float) -> float:
        """Fee charged at trade entry."""
        return amount_usd * (self.trading_fee_pct / 100.0)

    def resolution_fee(self, payout_usd: float) -> float:
        """Fee charged at resolution (currently 0 on Polymarket)."""
        return payout_usd * (self.resolution_fee_pct / 100.0)

    def total_cost(self, amount_usd: float) -> float:
        """Total cost to enter a position of amount_usd."""
        return amount_usd + self.trading_fee(amount_usd)

    def net_payout(self, gross_payout: float) -> float:
        """Net payout after resolution fee."""
        return gross_payout - self.resolution_fee(gross_payout)

    def net_pnl(self, capital: float, gross_payout: float) -> float:
        """
        Net P&L on a filled trade.
        capital = USD amount placed (before fees)
        gross_payout = value of shares at exit/resolution
        """
        entry_cost = self.total_cost(capital)
        net_out = self.net_payout(gross_payout)
        return net_out - entry_cost
