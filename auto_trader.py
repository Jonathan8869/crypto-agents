"""
Auto-Trader: Führt Trades auf Hyperliquid Testnet aus.
Nur bei HIGH Signals + Risk Manager Freigabe.
"""

import ccxt
import json
import os
from datetime import datetime, timezone
import config


class AutoTrader:
    def __init__(self):
        self.exchange = ccxt.hyperliquid({
            "apiKey": config.HYPERLIQUID_API_KEY,
            "secret": config.HYPERLIQUID_API_SECRET,
            "enableRateLimit": True,
            "options": {"defaultType": "swap"},
        })
        if config.HYPERLIQUID_TESTNET:
            self.exchange.set_sandbox_mode(True)

        self.trades_log = []
        self._load_trades()

    def _load_trades(self):
        trades_file = os.path.join(config.DATA_DIR, "trades_log.json")
        if os.path.exists(trades_file):
            try:
                self.trades_log = json.load(open(trades_file))
            except:
                self.trades_log = []

    def _save_trades(self):
        trades_file = os.path.join(config.DATA_DIR, "trades_log.json")
        json.dump(self.trades_log, open(trades_file, "w"), indent=2)

    def execute_trade(self, scan_result, risk_evaluation):
        """Führe einen Trade aus wenn Risk Manager freigegeben hat."""
        if not risk_evaluation.get("approved"):
            return None

        symbol = scan_result["symbol"]
        direction = scan_result.get("direction", "long")
        position = risk_evaluation.get("position", {})
        size = position.get("size", 0)
        entry = position.get("entry", 0)
        stop_loss = position.get("stop_loss", 0)

        if size <= 0 or entry <= 0:
            return None

        pair = f"{symbol}/USDC:USDC"
        side = "buy" if direction == "long" else "sell"

        try:
            # Market Order
            print(f"[AutoTrader] {side.upper()} {symbol}: Size={size}, Entry~${entry:,.2f}")
            order = self.exchange.create_order(
                symbol=pair,
                type="market",
                side=side,
                amount=size,
            )

            # Stop-Loss Order
            sl_side = "sell" if direction == "long" else "buy"
            sl_type = "stop"
            try:
                sl_order = self.exchange.create_order(
                    symbol=pair,
                    type=sl_type,
                    side=sl_side,
                    amount=size,
                    price=stop_loss,
                    params={"stopPrice": stop_loss, "reduceOnly": True}
                )
                sl_id = sl_order.get("id", "unknown")
            except Exception as e:
                print(f"[AutoTrader] Stop-Loss Fehler: {e}")
                sl_id = "failed"

            # Take-Profit Order
            rr = scan_result.get("rr_ratio", {})
            target = rr.get("target", 0)
            tp_id = "none"
            if target > 0:
                tp_side = "sell" if direction == "long" else "buy"
                try:
                    tp_order = self.exchange.create_order(
                        symbol=pair,
                        type="limit",
                        side=tp_side,
                        amount=size,
                        price=target,
                        params={"reduceOnly": True}
                    )
                    tp_id = tp_order.get("id", "unknown")
                except Exception as e:
                    print(f"[AutoTrader] Take-Profit Fehler: {e}")
                    tp_id = "failed"

            # Log trade
            trade_record = {
                "time": datetime.now(timezone.utc).isoformat(),
                "symbol": symbol,
                "direction": direction,
                "size": size,
                "entry_price": entry,
                "stop_loss": stop_loss,
                "take_profit": target,
                "order_id": order.get("id", "unknown"),
                "sl_order_id": sl_id,
                "tp_order_id": tp_id,
                "status": "open",
                "confluence": scan_result["confluence"]["level"],
                "pnl": 0.0
            }
            self.trades_log.append(trade_record)
            self._save_trades()

            return trade_record

        except Exception as e:
            print(f"[AutoTrader] Trade Fehler {symbol}: {e}")
            return {"error": str(e), "symbol": symbol}

    def format_trade_msg(self, trade):
        """Formatiere Trade-Nachricht für Telegram."""
        if not trade:
            return ""
        if "error" in trade:
            return f"❌ TRADE FEHLER: {trade['symbol']}\n{trade['error']}"

        emoji = "🟢" if trade["direction"] == "long" else "🔴"
        return f"""{emoji} AUTO-TRADE AUSGEFÜHRT
{'='*30}
{trade['symbol']} {trade['direction'].upper()}
Size: {trade['size']}
Entry: ~${trade['entry_price']:,.2f}
Stop-Loss: ${trade['stop_loss']:,.2f}
Take-Profit: ${trade['take_profit']:,.2f}
Order ID: {trade['order_id']}
{'='*30}
⚠️ TESTNET - Kein echtes Geld"""

    def get_open_positions(self):
        """Hole aktuelle offene Positionen."""
        try:
            positions = self.exchange.fetch_positions()
            open_pos = [p for p in positions if float(p.get("contracts", 0)) > 0]
            return open_pos
        except Exception as e:
            print(f"[AutoTrader] Positions-Fehler: {e}")
            return []

    def close_position(self, symbol, direction):
        """Schließe eine Position."""
        pair = f"{symbol}/USDC:USDC"
        try:
            positions = self.exchange.fetch_positions([pair])
            for pos in positions:
                contracts = float(pos.get("contracts", 0))
                if contracts > 0:
                    side = "sell" if direction == "long" else "buy"
                    order = self.exchange.create_order(
                        symbol=pair,
                        type="market",
                        side=side,
                        amount=contracts,
                        params={"reduceOnly": True}
                    )
                    print(f"[AutoTrader] Position geschlossen: {symbol}")
                    return order
        except Exception as e:
            print(f"[AutoTrader] Close Fehler: {e}")
            return None
