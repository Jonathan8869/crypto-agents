"""
Agent 2: The Quant Scanner
Scannt Kryptomärkte, berechnet Indikatoren, bewertet Setups nach Confluence.
"""

import ccxt
import pandas as pd
import pandas_ta as ta
from datetime import datetime, timezone, timedelta
import json
import os
import config


class QuantScanner:
    def __init__(self):
        self.exchange = ccxt.hyperliquid({
            "apiKey": config.HYPERLIQUID_API_KEY,
            "secret": config.HYPERLIQUID_API_SECRET,
            "enableRateLimit": True,
            "options": {"defaultType": "swap"},
        })
        if config.HYPERLIQUID_TESTNET:
            self.exchange.set_sandbox_mode(True)

        os.makedirs(config.SCAN_LOG_DIR, exist_ok=True)
        os.makedirs(config.LOG_DIR, exist_ok=True)

    def fetch_ohlcv(self, symbol: str, timeframe: str, limit: int = 200) -> pd.DataFrame:
        try:
            pair = f"{symbol}/USDC:USDC"
            ohlcv = self.exchange.fetch_ohlcv(pair, timeframe, limit=limit)
            df = pd.DataFrame(ohlcv, columns=["timestamp", "open", "high", "low", "close", "volume"])
            df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms")
            df.set_index("timestamp", inplace=True)
            return df
        except Exception as e:
            print(f"[Scanner] Fehler {symbol} {timeframe}: {e}")
            return pd.DataFrame()

    def fetch_funding_rate(self, symbol: str) -> float:
        try:
            pair = f"{symbol}/USDC:USDC"
            funding = self.exchange.fetch_funding_rate(pair)
            return funding.get("fundingRate", 0.0)
        except:
            return 0.0

    def calculate_indicators(self, df: pd.DataFrame) -> dict:
        if df.empty or len(df) < 50:
            return {}

        indicators = {}
        close = df["close"]
        volume = df["volume"]

        # RSI
        rsi = ta.rsi(close, length=config.RSI_PERIOD)
        if rsi is not None and len(rsi) > 0:
            val = rsi.iloc[-1]
            indicators["rsi"] = {
                "value": round(val, 2),
                "signal": "oversold" if val < config.RSI_OVERSOLD
                          else "overbought" if val > config.RSI_OVERBOUGHT
                          else "neutral",
                "triggered": val < config.RSI_OVERSOLD or val > config.RSI_OVERBOUGHT
            }

        # MACD
        macd = ta.macd(close, fast=config.MACD_FAST, slow=config.MACD_SLOW, signal=config.MACD_SIGNAL)
        if macd is not None and len(macd) > 1:
            m_line = macd.iloc[-1, 0]
            s_line = macd.iloc[-1, 2]
            prev_m = macd.iloc[-2, 0]
            prev_s = macd.iloc[-2, 2]
            bull_cross = prev_m < prev_s and m_line > s_line
            bear_cross = prev_m > prev_s and m_line < s_line
            indicators["macd"] = {
                "macd": round(m_line, 4),
                "signal_line": round(s_line, 4),
                "histogram": round(m_line - s_line, 4),
                "bullish_cross": bull_cross,
                "bearish_cross": bear_cross,
                "triggered": bull_cross or bear_cross
            }

        # Bollinger Bands
        bb = ta.bbands(close, length=config.BB_PERIOD, std=config.BB_STD)
        if bb is not None and len(bb) > 0:
            upper = bb.iloc[-1, 0]
            mid = bb.iloc[-1, 1]
            lower = bb.iloc[-1, 2]
            bw = (upper - lower) / mid if mid > 0 else 0
            price = close.iloc[-1]
            indicators["bollinger"] = {
                "upper": round(upper, 2),
                "middle": round(mid, 2),
                "lower": round(lower, 2),
                "bandwidth": round(bw, 4),
                "squeeze": bw < 0.04,
                "at_upper": price >= upper * 0.998,
                "at_lower": price <= lower * 1.002,
                "triggered": bw < 0.04 or price >= upper * 0.998 or price <= lower * 1.002
            }

        # EMA Ribbon
        emas = {}
        for period in config.EMA_PERIODS:
            ema = ta.ema(close, length=period)
            if ema is not None and len(ema) > 0:
                emas[period] = round(ema.iloc[-1], 2)

        if emas:
            price = close.iloc[-1]
            above_all = all(price > v for v in emas.values())
            below_all = all(price < v for v in emas.values())
            bullish_order = all(emas.get(config.EMA_PERIODS[i], 0) > emas.get(config.EMA_PERIODS[i + 1], 0)
                               for i in range(len(config.EMA_PERIODS) - 1) if config.EMA_PERIODS[i] in emas and config.EMA_PERIODS[i + 1] in emas)
            indicators["ema_ribbon"] = {
                "values": emas,
                "price_above_all": above_all,
                "price_below_all": below_all,
                "bullish_order": bullish_order,
                "triggered": above_all or below_all
            }

        # Volume
        if len(volume) >= 20:
            avg_vol = volume.rolling(20).mean().iloc[-1]
            cur_vol = volume.iloc[-1]
            spike = cur_vol / avg_vol if avg_vol > 0 else 0
            indicators["volume"] = {
                "current": round(cur_vol, 2),
                "avg_20": round(avg_vol, 2),
                "ratio": round(spike, 2),
                "spike": spike >= config.VOLUME_SPIKE_MULTIPLIER,
                "triggered": spike >= config.VOLUME_SPIKE_MULTIPLIER
            }

        # ATR for volatility check
        atr = ta.atr(df["high"], df["low"], close, length=14)
        if atr is not None and len(atr) > 0:
            atr_pct = (atr.iloc[-1] / close.iloc[-1]) * 100
            indicators["atr"] = {
                "value": round(atr.iloc[-1], 4),
                "percent": round(atr_pct, 2),
                "low_volatility": atr_pct < 1.0
            }

        # Support & Resistance (simple pivot)
        recent = df.tail(50)
        indicators["levels"] = {
            "support": round(recent["low"].min(), 2),
            "resistance": round(recent["high"].max(), 2),
            "current_price": round(close.iloc[-1], 2)
        }

        return indicators

    def score_confluence(self, all_tf_indicators: dict) -> dict:
        triggered_count = 0
        triggered_tfs = set()
        triggered_details = []

        for tf, indicators in all_tf_indicators.items():
            for name, data in indicators.items():
                if isinstance(data, dict) and data.get("triggered"):
                    triggered_count += 1
                    triggered_tfs.add(tf)
                    triggered_details.append(f"{name} ({tf})")

        if triggered_count >= config.HIGH_CONFLUENCE_MIN_INDICATORS and \
           len(triggered_tfs) >= config.HIGH_CONFLUENCE_MIN_TIMEFRAMES:
            level = "HIGH"
        elif triggered_count >= config.MEDIUM_CONFLUENCE_MIN:
            level = "MEDIUM"
        elif triggered_count > 0:
            level = "WATCH"
        else:
            level = "NONE"

        # Special rule: RSI + MACD + Volume = ALWAYS alert
        has_rsi = any("rsi" in ind and ind["rsi"].get("triggered")
                      for ind in all_tf_indicators.values())
        has_macd = any("macd" in ind and ind["macd"].get("triggered")
                       for ind in all_tf_indicators.values())
        has_volume = any("volume" in ind and ind["volume"].get("triggered")
                         for ind in all_tf_indicators.values())
        if has_rsi and has_macd and has_volume:
            level = "HIGH"

        return {
            "level": level,
            "triggered_count": triggered_count,
            "timeframes_triggered": len(triggered_tfs),
            "details": triggered_details
        }

    def calculate_rr_ratio(self, indicators: dict, direction: str = "long") -> dict:
        levels = None
        for tf_data in indicators.values():
            if "levels" in tf_data:
                levels = tf_data["levels"]
                break

        if not levels:
            return {"ratio": 0, "entry": 0, "stop": 0, "target": 0}

        price = levels["current_price"]
        support = levels["support"]
        resistance = levels["resistance"]

        if direction == "long":
            entry = price
            stop = support
            target = resistance
        else:
            entry = price
            stop = resistance
            target = support

        risk = abs(entry - stop)
        reward = abs(target - entry)
        ratio = reward / risk if risk > 0 else 0

        return {
            "ratio": round(ratio, 2),
            "entry": entry,
            "stop": round(stop, 2),
            "target": round(target, 2),
            "risk_pct": round((risk / entry) * 100, 2) if entry > 0 else 0
        }

    def scan_pair(self, symbol: str) -> dict:
        result = {
            "symbol": symbol,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "timeframe_data": {},
            "funding_rate": self.fetch_funding_rate(symbol),
        }

        for tf in config.TIMEFRAMES:
            df = self.fetch_ohlcv(symbol, tf)
            if not df.empty:
                indicators = self.calculate_indicators(df)
                result["timeframe_data"][tf] = indicators

        # Confluence scoring
        result["confluence"] = self.score_confluence(result["timeframe_data"])

        # Determine direction
        direction = "long"
        for tf_data in result["timeframe_data"].values():
            if "rsi" in tf_data and tf_data["rsi"].get("signal") == "overbought":
                direction = "short"
                break
            if "macd" in tf_data and tf_data["macd"].get("bearish_cross"):
                direction = "short"
                break

        result["rr_ratio"] = self.calculate_rr_ratio(result["timeframe_data"], direction)
        result["direction"] = direction

        # Funding rate flag
        fr = result["funding_rate"]
        result["funding_flag"] = fr > config.FUNDING_RATE_HIGH or fr < config.FUNDING_RATE_LOW

        # 24h change
        df_1h = self.fetch_ohlcv(symbol, "1h", limit=24)
        if not df_1h.empty and len(df_1h) >= 24:
            open_24h = df_1h["open"].iloc[0]
            close_now = df_1h["close"].iloc[-1]
            result["change_24h"] = round(((close_now - open_24h) / open_24h) * 100, 2)
        else:
            result["change_24h"] = 0.0

        return result

    def scan_all(self, pairs: list = None) -> list:
        if pairs is None:
            pairs = config.PRIMARY_PAIRS + config.SECONDARY_PAIRS

        results = []
        for symbol in pairs:
            print(f"[Scanner] Scanne {symbol}...")
            result = self.scan_pair(symbol)
            results.append(result)
            self._log_scan(result)

        return results

    def should_reduce_frequency(self, results: list) -> bool:
        """Prüfe ob niedrige Volatilität -> Scan-Intervall verdoppeln."""
        low_vol_count = 0
        for r in results:
            for tf_data in r.get("timeframe_data", {}).values():
                if "atr" in tf_data and tf_data["atr"].get("low_volatility"):
                    low_vol_count += 1
        return low_vol_count > len(results) * 2  # Mehr als die Hälfte der TFs

    def _log_scan(self, result: dict):
        date_str = datetime.now().strftime("%Y-%m-%d")
        log_file = os.path.join(config.SCAN_LOG_DIR, f"{date_str}.json")

        logs = []
        if os.path.exists(log_file):
            try:
                with open(log_file, "r") as f:
                    logs = json.load(f)
            except:
                logs = []

        # Kompakte Version loggen
        log_entry = {
            "symbol": result["symbol"],
            "time": result["timestamp"],
            "confluence": result["confluence"]["level"],
            "triggered": result["confluence"]["details"],
            "funding": result["funding_rate"],
        }
        logs.append(log_entry)

        with open(log_file, "w") as f:
            json.dump(logs, f, indent=2)
