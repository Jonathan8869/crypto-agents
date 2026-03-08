"""
Polymarket Analyzer
Fetcht und analysiert krypto-relevante Prediction Markets.
Keine Authentifizierung nötig für Lesezugriff.
"""

import requests
import json
import os
from datetime import datetime, timezone
import config


class PolymarketAnalyzer:
    def __init__(self):
        self.base_url = "https://gamma-api.polymarket.com"
        self.clob_url = "https://clob.polymarket.com"
        self.cache_file = os.path.join(config.DATA_DIR, "polymarket_cache.json")
        self.crypto_keywords = [
            "bitcoin", "btc", "ethereum", "eth", "solana", "sol",
            "crypto", "cryptocurrency", "blockchain", "defi",
            "dogecoin", "doge", "xrp", "ripple", "stablecoin",
            "sec", "etf", "binance", "coinbase", "fed", "rate cut",
            "interest rate", "inflation", "recession", "tariff"
        ]

    def fetch_markets(self, limit=50, active=True) -> list:
        """Hole aktive Polymarket Markets."""
        try:
            params = {
                "limit": limit,
                "active": str(active).lower(),
                "closed": "false",
                "order": "volume24hr",
                "ascending": "false"
            }
            r = requests.get(f"{self.base_url}/markets", params=params, timeout=15)
            if r.status_code == 200:
                return r.json()
            return []
        except Exception as e:
            print(f"[Polymarket] Fetch Fehler: {e}")
            return []

    def fetch_events(self, limit=20) -> list:
        """Hole aktive Events."""
        try:
            params = {
                "limit": limit,
                "active": "true",
                "closed": "false",
                "order": "volume24hr",
                "ascending": "false"
            }
            r = requests.get(f"{self.base_url}/events", params=params, timeout=15)
            if r.status_code == 200:
                return r.json()
            return []
        except Exception as e:
            print(f"[Polymarket] Events Fehler: {e}")
            return []

    def filter_crypto_relevant(self, markets: list) -> list:
        """Filtere nur krypto-relevante Markets."""
        relevant = []
        for m in markets:
            title = (m.get("question", "") + " " + m.get("description", "")).lower()
            for kw in self.crypto_keywords:
                if kw in title:
                    relevant.append(m)
                    break
        return relevant

    def filter_macro_relevant(self, markets: list) -> list:
        """Filtere makro-relevante Markets (Fed, Inflation, etc.)."""
        macro_kw = ["fed", "rate cut", "interest rate", "inflation", "recession",
                     "gdp", "unemployment", "cpi", "fomc", "tariff", "trade war",
                     "sanctions", "treasury", "dollar", "debt ceiling"]
        relevant = []
        for m in markets:
            title = (m.get("question", "") + " " + m.get("description", "")).lower()
            for kw in macro_kw:
                if kw in title:
                    relevant.append(m)
                    break
        return relevant

    def parse_market(self, m: dict) -> dict:
        """Parse ein Market-Objekt in ein lesbares Format."""
        outcomes = m.get("outcomes", [])
        prices = m.get("outcomePrices", [])

        # Parse prices
        if isinstance(prices, str):
            try:
                prices = json.loads(prices)
            except:
                prices = []

        parsed_outcomes = []
        for i, outcome in enumerate(outcomes):
            price = float(prices[i]) if i < len(prices) else 0
            parsed_outcomes.append({
                "name": outcome,
                "probability": round(price * 100, 1)
            })

        volume = float(m.get("volume", 0) or 0)
        volume_24h = float(m.get("volume24hr", 0) or 0)
        liquidity = float(m.get("liquidity", 0) or 0)

        return {
            "question": m.get("question", "Unknown"),
            "slug": m.get("slug", ""),
            "outcomes": parsed_outcomes,
            "volume_total": volume,
            "volume_24h": volume_24h,
            "liquidity": liquidity,
            "end_date": m.get("endDate", ""),
            "category": m.get("category", ""),
        }

    def get_crypto_markets(self) -> list:
        """Hole und parse krypto-relevante Markets."""
        all_markets = self.fetch_markets(limit=100)
        crypto = self.filter_crypto_relevant(all_markets)
        return [self.parse_market(m) for m in crypto[:15]]

    def get_macro_markets(self) -> list:
        """Hole und parse makro-relevante Markets."""
        all_markets = self.fetch_markets(limit=100)
        macro = self.filter_macro_relevant(all_markets)
        return [self.parse_market(m) for m in macro[:10]]

    def get_top_markets(self, limit=10) -> list:
        """Hole Top Markets nach Volume."""
        markets = self.fetch_markets(limit=limit)
        return [self.parse_market(m) for m in markets]

    def format_market_summary(self, markets: list, title: str = "POLYMARKET") -> str:
        """Formatiere Markets für Telegram."""
        if not markets:
            return f"📊 {title}: Keine relevanten Markets gefunden."

        msg = f"📊 {title}\n{'='*30}\n"
        for m in markets[:8]:
            q = m["question"][:80]
            outcomes_str = " | ".join(
                f"{o['name']}: {o['probability']}%" for o in m["outcomes"][:2]
            )
            vol = m["volume_24h"]
            if vol >= 1000000:
                vol_str = f"${vol/1000000:.1f}M"
            elif vol >= 1000:
                vol_str = f"${vol/1000:.0f}K"
            else:
                vol_str = f"${vol:.0f}"

            msg += f"\n{q}\n  {outcomes_str} | Vol: {vol_str}\n"

        return msg

    def get_sentiment_signals(self) -> dict:
        """Extrahiere Sentiment-Signale aus Polymarket Daten."""
        crypto_markets = self.get_crypto_markets()
        macro_markets = self.get_macro_markets()

        signals = {
            "crypto_markets_count": len(crypto_markets),
            "macro_markets_count": len(macro_markets),
            "highlights": [],
            "risk_signals": [],
            "bullish_signals": [],
        }

        for m in crypto_markets:
            for o in m["outcomes"]:
                # BTC/ETH ETF approvals, price targets
                q = m["question"].lower()
                if ("etf" in q or "bitcoin" in q or "btc" in q) and o["probability"] > 70:
                    signals["bullish_signals"].append(
                        f"{m['question'][:60]}: {o['name']} {o['probability']}%"
                    )
                if ("crash" in q or "ban" in q or "hack" in q) and o["probability"] > 40:
                    signals["risk_signals"].append(
                        f"{m['question'][:60]}: {o['name']} {o['probability']}%"
                    )

        for m in macro_markets:
            for o in m["outcomes"]:
                q = m["question"].lower()
                if "rate cut" in q and o["probability"] > 60:
                    signals["bullish_signals"].append(
                        f"Fed: {m['question'][:50]}: {o['name']} {o['probability']}%"
                    )
                if ("recession" in q or "crash" in q) and o["probability"] > 40:
                    signals["risk_signals"].append(
                        f"Macro: {m['question'][:50]}: {o['name']} {o['probability']}%"
                    )

        # Top highlights
        all_markets = crypto_markets + macro_markets
        sorted_by_vol = sorted(all_markets, key=lambda x: x["volume_24h"], reverse=True)
        for m in sorted_by_vol[:3]:
            top_outcome = max(m["outcomes"], key=lambda o: o["probability"]) if m["outcomes"] else None
            if top_outcome:
                signals["highlights"].append(
                    f"{m['question'][:60]}: {top_outcome['name']} {top_outcome['probability']}%"
                )

        # Cache
        try:
            with open(self.cache_file, "w") as f:
                json.dump({"time": datetime.now(timezone.utc).isoformat(), "signals": signals}, f, indent=2)
        except:
            pass

        return signals

    def format_sentiment(self, signals: dict) -> str:
        """Formatiere Sentiment für Telegram/Research."""
        msg = "🔮 POLYMARKET SENTIMENT\n" + "="*30 + "\n"

        if signals["bullish_signals"]:
            msg += "\n🟢 BULLISH:\n"
            for s in signals["bullish_signals"][:5]:
                msg += f"  {s}\n"

        if signals["risk_signals"]:
            msg += "\n🔴 RISIKEN:\n"
            for s in signals["risk_signals"][:5]:
                msg += f"  {s}\n"

        if signals["highlights"]:
            msg += "\n🔥 TOP MARKETS:\n"
            for h in signals["highlights"]:
                msg += f"  {h}\n"

        if not signals["bullish_signals"] and not signals["risk_signals"]:
            msg += "\nKeine starken Signale aktuell.\n"

        return msg
