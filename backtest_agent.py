"""
Agent 6: The Backtest Agent
Testet Strategien gegen historische Daten bevor echtes Geld riskiert wird.
"""

import json
import os
from datetime import datetime, timezone, timedelta
import pandas as pd
import pandas_ta as ta
import config


class BacktestAgent:
    def __init__(self, scanner):
        self.scanner = scanner
        os.makedirs(config.BACKTEST_CACHE_DIR, exist_ok=True)

    def _get_cache_key(self, symbol: str, setup_name: str) -> str:
        return f"{symbol}_{setup_name}".replace("/", "_")

    def _check_cache(self, symbol: str, setup_name: str) -> dict:
        cache_key = self._get_cache_key(symbol, setup_name)
        cache_file = os.path.join(config.BACKTEST_CACHE_DIR, f"{cache_key}.json")

        if os.path.exists(cache_file):
            try:
                with open(cache_file, "r") as f:
                    data = json.load(f)
                cached_date = datetime.fromisoformat(data["date"])
                if (datetime.now(timezone.utc) - cached_date).days < config.BACKTEST_CACHE_DAYS:
                    return data
            except:
                pass
        return {}

    def _save_cache(self, symbol: str, setup_name: str, result: dict):
        cache_key = self._get_cache_key(symbol, setup_name)
        cache_file = os.path.join(config.BACKTEST_CACHE_DIR, f"{cache_key}.json")
        result["date"] = datetime.now(timezone.utc).isoformat()
        with open(cache_file, "w") as f:
            json.dump(result, f, indent=2)

    def identify_setup(self, scan_result: dict) -> str:
        """Identifiziere den Setup-Typ basierend auf getriggerten Indikatoren."""
        details = scan_result.get("confluence", {}).get("details", [])
        indicators = set()
        for d in details:
            name = d.split("(")[0].strip().lower()
            indicators.add(name)

        if not indicators:
            return "no_setup"

        return "+".join(sorted(indicators))

    def backtest_signal(self, symbol: str, setup_name: str, direction: str = "long") -> dict:
        """Backteste ein Signal-Setup gegen historische Daten."""

        # Check cache
        cached = self._check_cache(symbol, setup_name)
        if cached:
            print(f"[Backtest] Cache hit für {symbol} {setup_name}")
            return cached

        print(f"[Backtest] Starte Backtest: {symbol} {setup_name} ({direction})")

        # Lade historische Daten (Daily für 180 Tage)
        df = self.scanner.fetch_ohlcv(symbol, "1d", limit=config.BACKTEST_DAYS)
        if df.empty or len(df) < 90:
            return {
                "symbol": symbol,
                "setup": setup_name,
                "recommendation": "VORSICHT",
                "reason": "Zu wenig historische Daten",
                "trades": 0
            }

        # Parse welche Indikatoren zum Setup gehören
        setup_indicators = setup_name.split("+")

        # Berechne Indikatoren auf gesamte Historie
        close = df["close"]
        volume = df["volume"]

        signals = pd.Series(False, index=df.index)

        # RSI Signal
        if "rsi" in setup_indicators:
            rsi = ta.rsi(close, length=config.RSI_PERIOD)
            if rsi is not None:
                if direction == "long":
                    signals = signals | (rsi < config.RSI_OVERSOLD)
                else:
                    signals = signals | (rsi > config.RSI_OVERBOUGHT)

        # MACD Signal
        if "macd" in setup_indicators:
            macd = ta.macd(close, fast=config.MACD_FAST, slow=config.MACD_SLOW, signal=config.MACD_SIGNAL)
            if macd is not None and len(macd) > 1:
                macd_line = macd.iloc[:, 0]
                signal_line = macd.iloc[:, 2]
                if direction == "long":
                    cross = (macd_line.shift(1) < signal_line.shift(1)) & (macd_line > signal_line)
                else:
                    cross = (macd_line.shift(1) > signal_line.shift(1)) & (macd_line < signal_line)
                signals = signals | cross

        # Bollinger Signal
        if "bollinger" in setup_indicators:
            bb = ta.bbands(close, length=config.BB_PERIOD, std=config.BB_STD)
            if bb is not None:
                if direction == "long":
                    signals = signals | (close <= bb.iloc[:, 2] * 1.002)  # At lower band
                else:
                    signals = signals | (close >= bb.iloc[:, 0] * 0.998)  # At upper band

        # Volume Signal
        if "volume" in setup_indicators:
            avg_vol = volume.rolling(20).mean()
            vol_spike = volume >= avg_vol * config.VOLUME_SPIKE_MULTIPLIER
            signals = signals & vol_spike  # Volume muss zusätzlich bestätigen

        # Simuliere Trades
        trades = []
        in_trade = False
        entry_price = 0
        entry_date = None

        for i in range(len(df)):
            if signals.iloc[i] and not in_trade:
                entry_price = close.iloc[i]
                entry_date = df.index[i]
                in_trade = True
                continue

            if in_trade:
                current = close.iloc[i]
                days_held = (df.index[i] - entry_date).days

                # Apply slippage and fees
                effective_entry = entry_price * (1 + config.SLIPPAGE) if direction == "long" \
                                 else entry_price * (1 - config.SLIPPAGE)

                if direction == "long":
                    pnl_pct = (current - effective_entry) / effective_entry
                    stop_hit = current <= effective_entry * 0.98  # 2% stop
                    tp_hit = current >= effective_entry * 1.04    # 1:2 R:R
                else:
                    pnl_pct = (effective_entry - current) / effective_entry
                    stop_hit = current >= effective_entry * 1.02
                    tp_hit = current <= effective_entry * 0.96

                # Exit conditions
                if stop_hit or tp_hit or days_held >= config.BACKTEST_TIMEOUT_HOURS / 24:
                    # Apply fees
                    pnl_pct -= config.TRADING_FEE * 2  # Entry + Exit fee

                    trades.append({
                        "entry_date": str(entry_date.date()),
                        "exit_date": str(df.index[i].date()),
                        "entry_price": round(effective_entry, 2),
                        "exit_price": round(current, 2),
                        "pnl_pct": round(pnl_pct * 100, 2),
                        "exit_reason": "stop" if stop_hit else "tp" if tp_hit else "timeout",
                        "days_held": days_held
                    })
                    in_trade = False

        # Berechne Statistiken
        if not trades:
            result = {
                "symbol": symbol,
                "setup": setup_name,
                "direction": direction,
                "period_days": config.BACKTEST_DAYS,
                "trades": 0,
                "recommendation": "VORSICHT",
                "reason": "Kein Vorkommen in historischen Daten"
            }
            self._save_cache(symbol, setup_name, result)
            return result

        wins = [t for t in trades if t["pnl_pct"] > 0]
        losses = [t for t in trades if t["pnl_pct"] <= 0]

        win_rate = len(wins) / len(trades) if trades else 0
        avg_win = sum(t["pnl_pct"] for t in wins) / len(wins) if wins else 0
        avg_loss = sum(t["pnl_pct"] for t in losses) / len(losses) if losses else 0
        best = max(t["pnl_pct"] for t in trades) if trades else 0
        worst = min(t["pnl_pct"] for t in trades) if trades else 0

        # Profit Factor
        gross_profit = sum(t["pnl_pct"] for t in wins) if wins else 0
        gross_loss = abs(sum(t["pnl_pct"] for t in losses)) if losses else 1
        profit_factor = gross_profit / gross_loss if gross_loss > 0 else 0

        # Max Drawdown
        cumulative = 0
        max_dd = 0
        for t in trades:
            cumulative += t["pnl_pct"]
            if cumulative < max_dd:
                max_dd = cumulative

        # Expected value per trade
        ev = (win_rate * avg_win) + ((1 - win_rate) * avg_loss)

        # Recommendation
        if len(trades) < config.BACKTEST_MIN_TRADES:
            recommendation = "VORSICHT"
            reason = f"Nur {len(trades)} Trades - zu wenig Daten"
        elif win_rate >= 0.50 and profit_factor >= config.BACKTEST_PF_TRADE:
            recommendation = "TRADEN"
            reason = f"Win-Rate {win_rate*100:.0f}% + PF {profit_factor:.2f}"
        elif win_rate >= 0.40 and profit_factor >= config.BACKTEST_PF_CAUTION:
            recommendation = "VORSICHT"
            reason = f"Okay aber nicht überzeugend (WR {win_rate*100:.0f}%)"
        else:
            recommendation = "ABLEHNEN"
            reason = f"Win-Rate {win_rate*100:.0f}% und PF {profit_factor:.2f} zu schwach"

        result = {
            "symbol": symbol,
            "setup": setup_name,
            "direction": direction,
            "period_days": config.BACKTEST_DAYS,
            "trades": len(trades),
            "win_rate": round(win_rate * 100, 1),
            "avg_win": round(avg_win, 2),
            "avg_loss": round(avg_loss, 2),
            "best_trade": round(best, 2),
            "worst_trade": round(worst, 2),
            "profit_factor": round(profit_factor, 2),
            "max_drawdown": round(max_dd, 2),
            "expected_value": round(ev, 2),
            "recommendation": recommendation,
            "reason": reason,
            "last_trade": trades[-1] if trades else None,
            "trade_details": trades
        }

        self._save_cache(symbol, setup_name, result)
        return result

    def format_backtest(self, result: dict, compact: bool = False) -> str:
        if compact:
            return (f"📊 Backtest {result['symbol']} [{result['setup']}]: "
                    f"WR {result.get('win_rate', 0)}% | PF {result.get('profit_factor', 0)} | "
                    f"{result['recommendation']}")

        emoji = {"TRADEN": "✅", "VORSICHT": "⚠️", "ABLEHNEN": "❌"}.get(result["recommendation"], "❓")

        msg = f"""{emoji} BACKTEST: {result['setup']} auf {result['symbol']}
━━━━━━━━━━━━━━━━━━
Zeitraum: letzte {result.get('period_days', 180)} Tage
Richtung: {result.get('direction', 'long').upper()}
Vorkommen: {result['trades']} mal
━━━━━━━━━━━━━━━━━━"""

        if result["trades"] > 0:
            msg += f"""
Win-Rate: {result.get('win_rate', 0)}%
Avg Gewinn: +{result.get('avg_win', 0)}%
Avg Verlust: {result.get('avg_loss', 0)}%
Bestes Ergebnis: +{result.get('best_trade', 0)}%
Schlechtestes: {result.get('worst_trade', 0)}%
Profit Factor: {result.get('profit_factor', 0)}
Max Drawdown: {result.get('max_drawdown', 0)}%
━━━━━━━━━━━━━━━━━━
EV pro Trade: {result.get('expected_value', 0)}%"""

            if result.get("last_trade"):
                lt = result["last_trade"]
                msg += f"\nLetzter Trade: {lt['entry_date']} → {lt['exit_date']} ({lt['pnl_pct']:+.2f}%)"

        msg += f"""
━━━━━━━━━━━━━━━━━━
EMPFEHLUNG: {result['recommendation']}
{result.get('reason', '')}

⚠️ Backtest-Ergebnisse garantieren KEINE zukünftigen Gewinne."""

        return msg
