"""
Agent 5: The Risk Manager
KEIN Trade passiert ohne Freigabe. Letzte Instanz.
"""

import json
import os
from datetime import datetime, timezone, timedelta
import config


class RiskManager:
    def __init__(self):
        os.makedirs(config.DATA_DIR, exist_ok=True)
        self.portfolio = self._load_portfolio()

    def _load_portfolio(self) -> dict:
        if os.path.exists(config.PORTFOLIO_FILE):
            try:
                with open(config.PORTFOLIO_FILE, "r") as f:
                    return json.load(f)
            except:
                pass
        return self._init_portfolio()

    def _init_portfolio(self) -> dict:
        portfolio = {
            "initial_value": config.INITIAL_PORTFOLIO_VALUE,
            "total_value": config.INITIAL_PORTFOLIO_VALUE,
            "available_balance": config.INITIAL_PORTFOLIO_VALUE,
            "positions": [],
            "closed_trades": [],
            "daily_pnl": 0.0,
            "weekly_pnl": 0.0,
            "monthly_pnl": 0.0,
            "consecutive_losses": 0,
            "last_updated": datetime.now(timezone.utc).isoformat()
        }
        self._save_portfolio(portfolio)
        return portfolio

    def _save_portfolio(self, portfolio: dict = None):
        if portfolio is None:
            portfolio = self.portfolio
        portfolio["last_updated"] = datetime.now(timezone.utc).isoformat()
        with open(config.PORTFOLIO_FILE, "w") as f:
            json.dump(portfolio, f, indent=2)

    def get_current_risk_per_trade(self) -> float:
        if self.portfolio["consecutive_losses"] >= config.CONSECUTIVE_LOSS_LIMIT:
            return config.REDUCED_RISK
        return config.MAX_RISK_PER_TRADE

    def calculate_position_size(self, entry: float, stop_loss: float) -> dict:
        risk_per_trade = self.get_current_risk_per_trade()
        portfolio_value = self.portfolio["total_value"]
        risk_amount = portfolio_value * risk_per_trade
        risk_per_unit = abs(entry - stop_loss)

        if risk_per_unit <= 0:
            return {"approved": False, "reason": "Stop-Loss gleich Entry", "size": 0}

        position_size = risk_amount / risk_per_unit
        position_value = position_size * entry

        # Check directional exposure
        current_exposure = sum(
            p["size"] * p["entry_price"]
            for p in self.portfolio["positions"]
            if p["direction"] == "long"
        ) - sum(
            p["size"] * p["entry_price"]
            for p in self.portfolio["positions"]
            if p["direction"] == "short"
        )

        max_exposure = portfolio_value * config.MAX_DIRECTIONAL_EXPOSURE
        if abs(current_exposure + position_value) > max_exposure:
            position_size = (max_exposure - abs(current_exposure)) / entry
            if position_size <= 0:
                return {"approved": False, "reason": "Max Directional Exposure erreicht", "size": 0}

        return {
            "approved": True,
            "size": round(position_size, 6),
            "value": round(position_size * entry, 2),
            "risk_amount": round(risk_amount, 2),
            "risk_pct": round(risk_per_trade * 100, 2),
            "entry": entry,
            "stop_loss": stop_loss
        }

    def evaluate_trade(self, scan_result: dict) -> dict:
        """Hauptfunktion: Bewerte ob ein Trade erlaubt ist."""
        symbol = scan_result["symbol"]
        confluence = scan_result["confluence"]
        rr = scan_result.get("rr_ratio", {})
        direction = scan_result.get("direction", "long")

        checks = []
        approved = True
        reasons = []

        # Check 1: R:R Ratio
        rr_ratio = rr.get("ratio", 0)
        if rr_ratio < config.MIN_RR_RATIO:
            approved = False
            reasons.append(f"R:R {rr_ratio:.1f} unter Minimum {config.MIN_RR_RATIO}")
        checks.append(f"R:R Ratio: {rr_ratio:.1f} {'✅' if rr_ratio >= config.MIN_RR_RATIO else '❌'}")

        # Check 2: Max open positions
        open_count = len(self.portfolio["positions"])
        if open_count >= config.MAX_OPEN_POSITIONS:
            approved = False
            reasons.append(f"Max Positionen erreicht ({open_count}/{config.MAX_OPEN_POSITIONS})")
        checks.append(f"Offene Positionen: {open_count}/{config.MAX_OPEN_POSITIONS} {'✅' if open_count < config.MAX_OPEN_POSITIONS else '❌'}")

        # Check 3: Korrelations-Schutz (BTC/ETH nicht gleiche Richtung)
        for pos in self.portfolio["positions"]:
            if (symbol in ["BTC", "ETH"] and pos["symbol"] in ["BTC", "ETH"]
                    and pos["direction"] == direction and symbol != pos["symbol"]):
                approved = False
                reasons.append(f"Korrelation: {pos['symbol']} bereits {direction}")
        checks.append(f"Korrelations-Check: {'✅' if not any('Korrelation' in r for r in reasons) else '❌'}")

        # Check 4: Daily loss limit
        if abs(self.portfolio["daily_pnl"]) >= config.MAX_DAILY_LOSS * 100:
            approved = False
            reasons.append(f"Tages-Limit erreicht: {self.portfolio['daily_pnl']:.2f}%")
        checks.append(f"Tages-P&L: {self.portfolio['daily_pnl']:+.2f}% {'✅' if abs(self.portfolio['daily_pnl']) < config.MAX_DAILY_LOSS * 100 else '❌'}")

        # Check 5: Weekly loss limit
        if abs(self.portfolio["weekly_pnl"]) >= config.MAX_WEEKLY_LOSS * 100:
            approved = False
            reasons.append("Wochen-Limit erreicht - Trading-Pause bis Montag")
        checks.append(f"Wochen-P&L: {self.portfolio['weekly_pnl']:+.2f}% {'✅' if abs(self.portfolio['weekly_pnl']) < config.MAX_WEEKLY_LOSS * 100 else '❌'}")

        # Check 6: Consecutive losses
        if self.portfolio["consecutive_losses"] >= config.CONSECUTIVE_LOSS_LIMIT:
            checks.append(f"⚠️ {self.portfolio['consecutive_losses']} Verluste in Folge - Risiko auf {config.REDUCED_RISK*100}% reduziert")

        # Check 7: Stop Loss muss existieren
        if rr.get("stop", 0) <= 0:
            approved = False
            reasons.append("Kein Stop-Loss definiert")
        checks.append(f"Stop-Loss: {'✅ Definiert' if rr.get('stop', 0) > 0 else '❌ Fehlt'}")

        # Calculate position size if approved
        position = {}
        if approved:
            entry = rr.get("entry", 0)
            stop = rr.get("stop", 0)
            position = self.calculate_position_size(entry, stop)
            if not position["approved"]:
                approved = False
                reasons.append(position.get("reason", "Position sizing fehlgeschlagen"))

        return {
            "symbol": symbol,
            "direction": direction,
            "approved": approved,
            "reasons": reasons,
            "checks": checks,
            "position": position,
            "confluence_level": confluence["level"],
            "rr_ratio": rr_ratio,
            "timestamp": datetime.now(timezone.utc).isoformat()
        }

    def format_evaluation(self, evaluation: dict) -> str:
        status = "✅ FREIGEGEBEN" if evaluation["approved"] else "❌ ABGELEHNT"
        msg = f"""🛡️ RISK CHECK: {evaluation['symbol']}
━━━━━━━━━━━━━━━━━━
Status: {status}
Richtung: {evaluation['direction'].upper()}
Confluence: {evaluation['confluence_level']}
━━━━━━━━━━━━━━━━━━
"""
        for check in evaluation["checks"]:
            msg += f"{check}\n"

        if evaluation["approved"] and evaluation["position"]:
            pos = evaluation["position"]
            msg += f"""━━━━━━━━━━━━━━━━━━
POSITION:
  Size: {pos['size']}
  Wert: ${pos['value']:,.2f}
  Risiko: ${pos['risk_amount']:,.2f} ({pos['risk_pct']}%)
  Entry: ${pos['entry']:,.2f}
  Stop: ${pos['stop_loss']:,.2f}"""

        if evaluation["reasons"]:
            msg += "\n━━━━━━━━━━━━━━━━━━\n"
            msg += "GRÜNDE:\n"
            for r in evaluation["reasons"]:
                msg += f"  ⚠️ {r}\n"

        return msg

    def portfolio_check(self) -> str:
        p = self.portfolio
        msg = f"""💰 PORTFOLIO CHECK
━━━━━━━━━━━━━━━━━━
Wert: ${p['total_value']:,.2f}
Verfügbar: ${p['available_balance']:,.2f}
━━━━━━━━━━━━━━━━━━
Tages-P&L: {p['daily_pnl']:+.2f}%
Wochen-P&L: {p['weekly_pnl']:+.2f}%
Monats-P&L: {p['monthly_pnl']:+.2f}%
━━━━━━━━━━━━━━━━━━
Offene Positionen: {len(p['positions'])}"""

        for pos in p["positions"]:
            msg += f"\n  {pos['symbol']} {pos['direction'].upper()}: {pos['size']} @ ${pos['entry_price']:,.2f}"

        # Warnings
        if abs(p['daily_pnl']) > 3:
            msg += f"\n\n⚠️ WARNUNG: Tages-P&L bei {p['daily_pnl']:+.2f}% (Limit: {config.MAX_DAILY_LOSS*100}%)"

        if p['consecutive_losses'] >= 2:
            msg += f"\n⚠️ {p['consecutive_losses']} Verluste in Folge"

        return msg

    def check_trailing_stops(self, current_prices: dict) -> list:
        """Prüfe Trailing Stops für alle offenen Positionen."""
        actions = []

        for pos in self.portfolio["positions"]:
            symbol = pos["symbol"]
            if symbol not in current_prices:
                continue

            current = current_prices[symbol]
            entry = pos["entry_price"]
            direction = pos["direction"]

            if direction == "long":
                pnl_pct = (current - entry) / entry
            else:
                pnl_pct = (entry - current) / entry

            # Trailing Stop Logic
            if pnl_pct >= config.TRAILING_70:
                new_stop = entry + (current - entry) * 0.70 if direction == "long" \
                          else entry - (entry - current) * 0.70
                actions.append({
                    "symbol": symbol,
                    "action": "trailing_70",
                    "new_stop": round(new_stop, 2),
                    "pnl_pct": round(pnl_pct * 100, 2),
                    "msg": f"📈 {symbol}: +{pnl_pct*100:.1f}% - Trailing Stop auf 70% Gewinn (${new_stop:,.2f})"
                })
            elif pnl_pct >= config.TRAILING_50:
                new_stop = entry + (current - entry) * 0.50 if direction == "long" \
                          else entry - (entry - current) * 0.50
                actions.append({
                    "symbol": symbol,
                    "action": "trailing_50",
                    "new_stop": round(new_stop, 2),
                    "pnl_pct": round(pnl_pct * 100, 2),
                    "msg": f"📈 {symbol}: +{pnl_pct*100:.1f}% - Trailing Stop auf 50% Gewinn (${new_stop:,.2f})"
                })
            elif pnl_pct >= config.TRAILING_BREAKEVEN:
                actions.append({
                    "symbol": symbol,
                    "action": "breakeven",
                    "new_stop": entry,
                    "pnl_pct": round(pnl_pct * 100, 2),
                    "msg": f"📈 {symbol}: +{pnl_pct*100:.1f}% - Stop auf Break-Even (${entry:,.2f})"
                })

        return actions
