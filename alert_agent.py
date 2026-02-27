"""
Agent 4: The Alert Agent
Formatiert und liefert Benachrichtigungen via Telegram.
"""

import json
import os
from datetime import datetime, timezone, timedelta
import pytz
import requests
import config


class AlertAgent:
    def __init__(self):
        self.tz = pytz.timezone(config.TIMEZONE)
        self.alert_history = []
        self.cooldowns = {}  # {coin: last_alert_time}
        os.makedirs(config.DATA_DIR, exist_ok=True)
        self._load_history()

    def _load_history(self):
        if os.path.exists(config.ALERTS_LOG):
            try:
                with open(config.ALERTS_LOG, "r") as f:
                    self.alert_history = json.load(f)
            except:
                self.alert_history = []

    def _save_history(self):
        with open(config.ALERTS_LOG, "w") as f:
            json.dump(self.alert_history[-500:], f, indent=2)  # Keep last 500

    def is_quiet_hours(self) -> bool:
        now = datetime.now(self.tz)
        hour = now.hour
        if config.QUIET_HOURS_START > config.QUIET_HOURS_END:
            return hour >= config.QUIET_HOURS_START or hour < config.QUIET_HOURS_END
        return config.QUIET_HOURS_START <= hour < config.QUIET_HOURS_END

    def is_weekend(self) -> bool:
        return datetime.now(self.tz).weekday() >= 5

    def check_cooldown(self, symbol: str) -> bool:
        if symbol in self.cooldowns:
            elapsed = (datetime.now(timezone.utc) - self.cooldowns[symbol]).total_seconds() / 60
            return elapsed < config.ALERT_COOLDOWN_MINUTES
        return False

    def count_recent_alerts(self) -> int:
        one_hour_ago = datetime.now(timezone.utc) - timedelta(hours=1)
        return sum(1 for a in self.alert_history
                   if datetime.fromisoformat(a["time"]) > one_hour_ago)

    def should_send(self, symbol: str, level: str) -> tuple:
        """Returns (should_send: bool, reason: str)"""
        # WATCH level nie einzeln senden
        if level == "WATCH":
            return False, "WATCH-Level nur im Daily Summary"

        if level == "NONE":
            return False, "Kein Signal"

        # Quiet hours: nur HIGH
        if self.is_quiet_hours() and level != "HIGH":
            return False, f"Ruhezeit - nur HIGH Alerts ({config.QUIET_HOURS_START}-{config.QUIET_HOURS_END} Uhr)"

        # Weekend: nur HIGH
        if self.is_weekend() and level != "HIGH":
            return False, "Wochenende - nur HIGH Alerts"

        # Cooldown check (außer HIGH)
        if level != "HIGH" and self.check_cooldown(symbol):
            return False, f"Cooldown für {symbol} aktiv"

        # Max alerts per hour
        if self.count_recent_alerts() >= config.MAX_ALERTS_PER_HOUR and level != "HIGH":
            return False, "Max Alerts pro Stunde erreicht"

        return True, "OK"

    def format_signal_alert(self, scan_result: dict, researcher_context: str = "") -> str:
        symbol = scan_result["symbol"]
        confluence = scan_result["confluence"]
        level = confluence["level"]
        rr = scan_result.get("rr_ratio", {})
        direction = scan_result.get("direction", "long")
        change_24h = scan_result.get("change_24h", 0)
        funding = scan_result.get("funding_rate", 0)

        # Emoji based on direction and level
        if level == "HIGH":
            emoji = "🟢" if direction == "long" else "🔴"
        else:
            emoji = "🟡"

        # Get price and levels from first available timeframe
        price = 0
        support = 0
        resistance = 0
        for tf_data in scan_result.get("timeframe_data", {}).values():
            if "levels" in tf_data:
                price = tf_data["levels"]["current_price"]
                support = tf_data["levels"]["support"]
                resistance = tf_data["levels"]["resistance"]
                break

        # Format triggered indicators
        details = confluence.get("details", [])
        indicators_str = "\n".join(f"  • {d}" for d in details[:6])

        msg = f"""{emoji} SIGNAL CHANGE: {symbol}/USDT
━━━━━━━━━━━━━━━━━━
Preis: ${price:,.2f} | 24h: {'+' if change_24h >= 0 else ''}{change_24h}%
Richtung: {'LONG 📈' if direction == 'long' else 'SHORT 📉'}
Confidence: {level}
Funding: {funding:.4f}%
━━━━━━━━━━━━━━━━━━
Indikatoren:
{indicators_str}
━━━━━━━━━━━━━━━━━━
Support: ${support:,.2f}
Resistance: ${resistance:,.2f}
R:R Ratio: 1:{rr.get('ratio', 0):.1f}
Risiko: {rr.get('risk_pct', 0)}%
━━━━━━━━━━━━━━━━━━"""

        # Action based on level
        if level == "HIGH":
            msg += "\nAktion: ⚡ Prepare Entry"
        elif level == "MEDIUM":
            msg += "\nAktion: 👀 Watch closely"

        if researcher_context:
            msg += f"\n\nKontext: {researcher_context[:150]}"

        return msg

    def format_risk_warning(self, warning_type: str, details: str) -> str:
        return f"""🚨 RISK WARNING: {warning_type}
━━━━━━━━━━━━━━━━━━
{details}
━━━━━━━━━━━━━━━━━━
Aktion: Sofort prüfen"""

    def format_daily_summary(self, scan_results: list, research: str = "", portfolio: dict = None) -> str:
        now = datetime.now(self.tz).strftime("%d.%m.%Y %H:%M")
        msg = f"""📊 DAILY SUMMARY - {now}
━━━━━━━━━━━━━━━━━━\n"""

        # Signals
        high = [r for r in scan_results if r["confluence"]["level"] == "HIGH"]
        medium = [r for r in scan_results if r["confluence"]["level"] == "MEDIUM"]
        watch = [r for r in scan_results if r["confluence"]["level"] == "WATCH"]

        if high:
            msg += "\n🔴 HIGH SIGNALS:\n"
            for r in high:
                msg += f"  {r['symbol']}: {', '.join(r['confluence']['details'][:3])}\n"

        if medium:
            msg += "\n🟡 MEDIUM SIGNALS:\n"
            for r in medium:
                msg += f"  {r['symbol']}: {', '.join(r['confluence']['details'][:2])}\n"

        if watch:
            msg += "\n👀 WATCHLIST:\n"
            for r in watch:
                msg += f"  {r['symbol']}: {', '.join(r['confluence']['details'][:2])}\n"

        if not high and not medium and not watch:
            msg += "\nKeine aktiven Signals. Markt ruhig.\n"

        # Portfolio
        if portfolio:
            msg += f"\n💰 PORTFOLIO:\n"
            msg += f"  Wert: ${portfolio.get('total_value', 0):,.2f}\n"
            msg += f"  Tages-P&L: {portfolio.get('daily_pnl', 0):+.2f}%\n"
            msg += f"  Offene Positionen: {portfolio.get('open_positions', 0)}\n"

        # Research excerpt
        if research:
            msg += f"\n📰 RESEARCH:\n{research[:300]}\n"

        msg += "\n━━━━━━━━━━━━━━━━━━"
        return msg

    def format_morning_briefing(self, scan_results: list, research: str = "") -> str:
        now = datetime.now(self.tz).strftime("%d.%m.%Y")
        msg = f"""☀️ MORNING BRIEFING - {now}
━━━━━━━━━━━━━━━━━━\n"""

        # Nur relevante Veränderungen
        changes = [r for r in scan_results if r["confluence"]["level"] in ["HIGH", "MEDIUM"]]

        if not changes:
            return ""  # Kein Briefing wenn nichts passiert

        for r in changes:
            emoji = "🔴" if r["confluence"]["level"] == "HIGH" else "🟡"
            msg += f"\n{emoji} {r['symbol']}: {r['confluence']['level']}\n"
            msg += f"  24h: {r.get('change_24h', 0):+.2f}%\n"
            for detail in r["confluence"]["details"][:3]:
                msg += f"  • {detail}\n"

        if research:
            msg += f"\n📰 {research[:200]}\n"

        msg += "\n━━━━━━━━━━━━━━━━━━"
        return msg

    def format_weekly_review(self) -> str:
        # Analysiere Alert-Historie der letzten Woche
        one_week_ago = datetime.now(timezone.utc) - timedelta(days=7)
        weekly_alerts = [a for a in self.alert_history
                         if datetime.fromisoformat(a["time"]) > one_week_ago]

        total = len(weekly_alerts)
        high_count = sum(1 for a in weekly_alerts if a.get("level") == "HIGH")
        medium_count = sum(1 for a in weekly_alerts if a.get("level") == "MEDIUM")

        msg = f"""📈 WÖCHENTLICHE REVIEW
━━━━━━━━━━━━━━━━━━
Alerts diese Woche: {total}
  HIGH: {high_count}
  MEDIUM: {medium_count}
━━━━━━━━━━━━━━━━━━"""

        return msg

    def send_telegram(self, message: str) -> bool:
        if not config.TELEGRAM_CHAT_ID:
            print("[Alert] Keine Telegram Chat ID konfiguriert")
            return False

        try:
            url = f"https://api.telegram.org/bot{config.TELEGRAM_BOT_TOKEN}/sendMessage"
            response = requests.post(url, json={
                "chat_id": config.TELEGRAM_CHAT_ID,
                "text": message,
                "parse_mode": "HTML"
            }, timeout=10)
            return response.status_code == 200
        except Exception as e:
            print(f"[Alert] Telegram Fehler: {e}")
            return False

    def process_scan_results(self, scan_results: list, researcher_context: str = "") -> list:
        sent_alerts = []

        # Gruppiere verwandte Alerts
        alertable = [r for r in scan_results if r["confluence"]["level"] in ["HIGH", "MEDIUM"]]

        for result in alertable:
            symbol = result["symbol"]
            level = result["confluence"]["level"]

            should, reason = self.should_send(symbol, level)
            if not should:
                print(f"[Alert] {symbol} übersprungen: {reason}")
                continue

            msg = self.format_signal_alert(result, researcher_context)
            success = self.send_telegram(msg)

            if success:
                self.cooldowns[symbol] = datetime.now(timezone.utc)
                alert_record = {
                    "time": datetime.now(timezone.utc).isoformat(),
                    "symbol": symbol,
                    "level": level,
                    "direction": result.get("direction"),
                    "price": result.get("rr_ratio", {}).get("entry", 0),
                    "details": result["confluence"]["details"]
                }
                self.alert_history.append(alert_record)
                sent_alerts.append(alert_record)

        self._save_history()
        return sent_alerts
