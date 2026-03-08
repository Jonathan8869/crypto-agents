"""
Agent 1: The Coordinator + Telegram Bot + Scheduler
Orchestriert das gesamte System. Delegiert, priorisiert, liefert Briefings.
Telegram-Bot für Operator-Interaktion.
"""

import asyncio
import json
import os
import signal
import sys
import requests
from datetime import datetime, timezone
from threading import Thread
import pytz

from telegram import Update, BotCommand
from telegram.ext import (
    Application, CommandHandler, MessageHandler,
    filters, ContextTypes
)
import schedule
import time

import config
from quant_scanner import QuantScanner
from researcher import Researcher
from alert_agent import AlertAgent
from risk_manager import RiskManager
from backtest_agent import BacktestAgent
from auto_trader import AutoTrader

# Initialize all agents
scanner = QuantScanner()
researcher = Researcher()
alert_agent = AlertAgent()
risk_manager = RiskManager()
backtest = BacktestAgent(scanner)
trader = AutoTrader()

tz = pytz.timezone(config.TIMEZONE)


# ============================================================
# COORDINATOR LOGIC
# ============================================================

def run_primary_scan():
    """Scan BTC, ETH alle 15 Min."""
    print(f"[Coordinator] Primary Scan gestartet: {datetime.now(tz).strftime('%H:%M')}")
    results = scanner.scan_all(config.PRIMARY_PAIRS)
    process_results(results)


def run_secondary_scan():
    """Scan SOL, DOGE, XRP alle 60 Min."""
    print(f"[Coordinator] Secondary Scan gestartet: {datetime.now(tz).strftime('%H:%M')}")
    results = scanner.scan_all(config.SECONDARY_PAIRS)
    process_results(results)


def run_full_scan():
    """Scan aller Pairs."""
    print(f"[Coordinator] Full Scan gestartet: {datetime.now(tz).strftime('%H:%M')}")
    results = scanner.scan_all()
    process_results(results)
    return results


def process_results(results: list):
    """Verarbeite Scan-Ergebnisse: Alerts, Risk Checks, Backtests."""
    for result in results:
        level = result["confluence"]["level"]

        if level == "HIGH":
            # Backtest für HIGH signals
            setup_name = backtest.identify_setup(result)
            bt_result = backtest.backtest_signal(
                result["symbol"], setup_name, result.get("direction", "long")
            )

            # Downgrade wenn Backtest schlecht
            if bt_result.get("win_rate", 0) < 40 and bt_result.get("trades", 0) >= 10:
                result["confluence"]["level"] = "WATCH"
                result["confluence"]["details"].append("⚠️ Backtest Win-Rate unter 40% - Downgrade")
                print(f"[Coordinator] {result['symbol']} von HIGH auf WATCH downgraded (Backtest)")
                continue

            # Risk Check
            evaluation = risk_manager.evaluate_trade(result)

            # Researcher Kontext
            context = ""
            try:
                context = researcher.analyze_narrative_shift(f"{result['symbol']} {level} signal")
            except:
                pass

            # Alert senden
            msg = alert_agent.format_signal_alert(result, context)
            if bt_result.get("trades", 0) > 0:
                msg += f"\n\n{backtest.format_backtest(bt_result, compact=True)}"
            msg += f"\n\n{risk_manager.format_evaluation(evaluation)}"

            # AUTO-TRADE: Bei Freigabe automatisch traden
            if evaluation.get("approved"):
                trade = trader.execute_trade(result, evaluation)
                if trade and "error" not in trade:
                    # NUR bei echtem Trade Nachricht senden
                    msg = alert_agent.format_signal_alert(result, context)
                    if bt_result.get("trades", 0) > 0:
                        msg += f"\n\n{backtest.format_backtest(bt_result, compact=True)}"
                    msg += f"\n\n{trader.format_trade_msg(trade)}"
                    alert_agent.send_telegram(msg)
                elif trade and "error" in trade:
                    alert_agent.send_telegram(f"❌ Trade fehlgeschlagen: {trade['symbol']}\n{trade['error']}")
            # Kein Trade = keine Nachricht

        # MEDIUM: nur loggen, keine Nachricht


def morning_briefing():
    """Morning Briefing um 8:00 - nur wenn relevant."""
    now = datetime.now(tz)
    if now.weekday() >= 5:  # Wochenende
        return

    results = run_full_scan()
    research = researcher.daily_research(results)
    msg = alert_agent.format_morning_briefing(results, research)
    if msg:  # Nur senden wenn es Veränderungen gibt
        alert_agent.send_telegram(msg)


def daily_research_job():
    """Research um 7:00."""
    now = datetime.now(tz)
    if now.weekday() >= 5:
        return
    # Research wird im Morning Briefing mitgeliefert
    pass


def eod_summary():
    """End-of-Day Summary um 22:00."""
    results = run_full_scan()
    research = researcher.daily_research(results)
    portfolio = risk_manager.portfolio
    msg = alert_agent.format_daily_summary(results, research, portfolio)
    alert_agent.send_telegram(msg)


def portfolio_check_job():
    """Portfolio Check 2x täglich."""
    msg = risk_manager.portfolio_check()
    alert_agent.send_telegram(msg)


def weekly_deep_dive():
    """Wöchentlicher Deep Dive (Montag)."""
    results = run_full_scan()
    report = researcher.weekly_deep_dive(results)
    alert_agent.send_telegram(f"📚 WÖCHENTLICHER DEEP DIVE\n━━━━━━━━━━━━━━━━━━\n{report}")


def weekly_review():
    """Wöchentliche Review (Sonntag)."""
    msg = alert_agent.format_weekly_review()
    alert_agent.send_telegram(msg)


# ============================================================
# TELEGRAM BOT COMMANDS
# ============================================================

async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    config.TELEGRAM_CHAT_ID = str(update.effective_chat.id)
    # Save chat ID
    with open(os.path.join(config.DATA_DIR, "chat_id.txt"), "w") as f:
        f.write(config.TELEGRAM_CHAT_ID)

    await update.message.reply_text(
        "🦀 Crypto Trading Agents aktiv!\n\n"
        "Befehle:\n"
        "/scan - Sofort alle Pairs scannen\n"
        "/scan_btc - Nur BTC scannen\n"
        "/scan_eth - Nur ETH scannen\n"
        "/status - Portfolio Status\n"
        "/research - Research Brief\n"
        "/backtest [COIN] - Backtest letztes Signal\n"
        "/risk - Risk Check\n"
        "/briefing - Morning Briefing\n"
        "/summary - Daily Summary\n"
        "/weekly - Weekly Deep Dive\n"
        "/alerts - Letzte Alerts\n"
        "/help - Alle Befehle"
    )


async def cmd_scan(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("🔍 Scanne alle Pairs...")
    results = run_full_scan()
    for r in results:
        level = r["confluence"]["level"]
        if level != "NONE":
            msg = alert_agent.format_signal_alert(r)
            await update.message.reply_text(msg)
    if all(r["confluence"]["level"] == "NONE" for r in results):
        await update.message.reply_text("✅ Keine aktiven Signals. Markt ruhig.")


async def cmd_scan_single(update: Update, context: ContextTypes.DEFAULT_TYPE):
    cmd = update.message.text.split("_")
    if len(cmd) < 2:
        await update.message.reply_text("Nutze: /scan_btc, /scan_eth, /scan_sol, /scan_doge, /scan_xrp")
        return

    symbol = cmd[1].upper()
    if symbol not in config.PRIMARY_PAIRS + config.SECONDARY_PAIRS:
        await update.message.reply_text(f"❌ {symbol} nicht in der Watchlist")
        return

    await update.message.reply_text(f"🔍 Scanne {symbol}...")
    result = scanner.scan_pair(symbol)
    msg = alert_agent.format_signal_alert(result)
    await update.message.reply_text(msg)


async def cmd_status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = risk_manager.portfolio_check()
    await update.message.reply_text(msg)


async def cmd_research(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("📰 Generiere Research Brief...")
    results = scanner.scan_all(config.PRIMARY_PAIRS)
    report = researcher.daily_research(results)
    await update.message.reply_text(f"📰 RESEARCH BRIEF\n━━━━━━━━━━━━━━━━━━\n{report}")


async def cmd_backtest(update: Update, context: ContextTypes.DEFAULT_TYPE):
    args = context.args
    if not args:
        await update.message.reply_text("Nutze: /backtest BTC oder /backtest ETH")
        return

    symbol = args[0].upper()
    await update.message.reply_text(f"📊 Backteste {symbol}...")

    result = scanner.scan_pair(symbol)
    setup_name = backtest.identify_setup(result)

    if setup_name == "no_setup":
        await update.message.reply_text(f"Kein aktives Setup für {symbol} zum Backtesten.")
        return

    bt_result = backtest.backtest_signal(symbol, setup_name, result.get("direction", "long"))
    msg = backtest.format_backtest(bt_result)
    await update.message.reply_text(msg)


async def cmd_risk(update: Update, context: ContextTypes.DEFAULT_TYPE):
    results = scanner.scan_all(config.PRIMARY_PAIRS)
    high_signals = [r for r in results if r["confluence"]["level"] in ["HIGH", "MEDIUM"]]

    if not high_signals:
        await update.message.reply_text("✅ Keine aktiven Signals für Risk Check.")
        return

    for result in high_signals:
        evaluation = risk_manager.evaluate_trade(result)
        msg = risk_manager.format_evaluation(evaluation)
        await update.message.reply_text(msg)


async def cmd_briefing(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("☀️ Generiere Briefing...")
    results = run_full_scan()
    research = researcher.daily_research(results)
    msg = alert_agent.format_morning_briefing(results, research)
    if msg:
        await update.message.reply_text(msg)
    else:
        await update.message.reply_text("☀️ Keine relevanten Veränderungen. Alles ruhig.")


async def cmd_summary(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("📊 Generiere Summary...")
    results = run_full_scan()
    research = researcher.daily_research(results)
    portfolio = risk_manager.portfolio
    msg = alert_agent.format_daily_summary(results, research, portfolio)
    await update.message.reply_text(msg)


async def cmd_weekly(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("📚 Generiere Weekly Deep Dive...")
    results = run_full_scan()
    report = researcher.weekly_deep_dive(results)
    await update.message.reply_text(f"📚 WÖCHENTLICHER DEEP DIVE\n━━━━━━━━━━━━━━━━━━\n{report}")


async def cmd_alerts(update: Update, context: ContextTypes.DEFAULT_TYPE):
    recent = alert_agent.alert_history[-10:]
    if not recent:
        await update.message.reply_text("Keine Alerts bisher.")
        return

    msg = "📋 LETZTE ALERTS:\n━━━━━━━━━━━━━━━━━━\n"
    for a in reversed(recent):
        t = datetime.fromisoformat(a["time"]).strftime("%d.%m %H:%M")
        msg += f"{t} | {a['symbol']} | {a['level']} | {a.get('direction', '?')}\n"
    await update.message.reply_text(msg)


async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🦀 CRYPTO AGENTS - BEFEHLE\n"
        "━━━━━━━━━━━━━━━━━━\n"
        "📊 SCANNER\n"
        "  /scan - Alle Pairs scannen\n"
        "  /scan_btc - Nur BTC\n"
        "  /scan_eth - Nur ETH\n"
        "  /scan_sol - Nur SOL\n"
        "  /scan_doge - Nur DOGE\n"
        "  /scan_xrp - Nur XRP\n\n"
        "📰 RESEARCH\n"
        "  /research - Research Brief\n"
        "  /weekly - Weekly Deep Dive\n\n"
        "🛡️ RISK\n"
        "  /status - Portfolio Status\n"
        "  /risk - Risk Check\n\n"
        "📊 BACKTEST\n"
        "  /backtest BTC - Backtest Signal\n\n"
        "💰 TRADING\n"
        "  /positions - Offene Positionen\n"
        "  /trades - Trade Historie\n"
        "  /close BTC - Position schliessen\n\n"
        "📋 REPORTS\n"
        "  /briefing - Morning Briefing\n"
        "  /summary - Daily Summary\n"
        "  /alerts - Letzte Alerts\n"
    )


async def cmd_positions(update: Update, context: ContextTypes.DEFAULT_TYPE):
    positions = trader.get_open_positions()
    if not positions:
        await update.message.reply_text("Keine offenen Positionen.")
        return
    msg = "💰 OFFENE POSITIONEN:\n" + "="*30 + "\n"
    for p in positions:
        sym = p.get("symbol", "?")
        side = p.get("side", "?")
        size = p.get("contracts", 0)
        pnl = p.get("unrealizedPnl", 0)
        entry = p.get("entryPrice", 0)
        msg += f"{sym} {side}: {size} @ ${float(entry):,.2f} | PnL: ${float(pnl):,.2f}\n"
    await update.message.reply_text(msg)


async def cmd_trades(update: Update, context: ContextTypes.DEFAULT_TYPE):
    recent = trader.trades_log[-10:]
    if not recent:
        await update.message.reply_text("Keine Trades bisher.")
        return
    msg = "📋 TRADE HISTORIE:\n" + "="*30 + "\n"
    for t in reversed(recent):
        time_str = datetime.fromisoformat(t["time"]).strftime("%d.%m %H:%M")
        emoji = "🟢" if t["direction"] == "long" else "🔴"
        msg += f"{emoji} {time_str} | {t['symbol']} {t['direction']} | Size: {t['size']} | {t['status']}\n"
    await update.message.reply_text(msg)


async def cmd_close(update: Update, context: ContextTypes.DEFAULT_TYPE):
    args = context.args
    if not args:
        await update.message.reply_text("Nutze: /close BTC oder /close ETH")
        return
    symbol = args[0].upper()
    await update.message.reply_text(f"Schliesse {symbol} Position...")
    # Find direction from trades log
    direction = "long"
    for t in reversed(trader.trades_log):
        if t["symbol"] == symbol and t["status"] == "open":
            direction = t["direction"]
            break
    result = trader.close_position(symbol, direction)
    if result:
        await update.message.reply_text(f"✅ {symbol} Position geschlossen!")
    else:
        await update.message.reply_text(f"❌ Keine offene {symbol} Position gefunden.")


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Normale Nachrichten mit Kimi K2.5 beantworten."""
    user_msg = update.message.text
    if not user_msg:
        return

    # Lade aktuellen Kontext
    portfolio = risk_manager.portfolio
    recent_alerts = alert_agent.alert_history[-5:]
    alerts_str = ""
    for a in recent_alerts:
        alerts_str += f"- {a['symbol']} {a['level']} {a.get('direction','?')} ({a['time'][:16]})\n"

    positions_str = ""
    for p in portfolio.get("positions", []):
        positions_str += f"- {p['symbol']} {p['direction']} @ ${p.get('entry_price',0):,.2f}\n"

    system_prompt = f"""Du bist ein Krypto-Trading Assistent. Du sprichst Deutsch.
Du hast Zugriff auf folgende Live-Daten:

PORTFOLIO:
Wert: ${portfolio.get('total_value', 10000):,.2f}
Tages-P&L: {portfolio.get('daily_pnl', 0):+.2f}%

OFFENE POSITIONEN:
{positions_str if positions_str else 'Keine'}

LETZTE ALERTS:
{alerts_str if alerts_str else 'Keine'}

REGELN:
- Antworte kurz und praezise (max 200 Woerter)
- Gib keine Finanzberatung, nur Analyse
- Wenn gefragt nach Scans/Trades, verweise auf die / Befehle
- Sei ehrlich wenn du etwas nicht weisst"""

    try:
        r = requests.post("http://172.17.0.1:32768/v1/chat/completions", json={
            "model": "kimi-k2.5:cloud",
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_msg}
            ],
            "stream": False,
            "max_tokens": 500
        }, timeout=120)
        reply = r.json()["choices"][0]["message"]["content"]
        await update.message.reply_text(reply)
    except Exception as e:
        await update.message.reply_text(f"Fehler bei Kimi: {e}")


# ============================================================
# SCHEDULER (Background Thread)
# ============================================================

def run_scheduler():
    """Scheduler läuft im Background Thread."""
    # Primary Scan: alle 15 Min
    schedule.every(config.PRIMARY_SCAN_INTERVAL).minutes.do(run_primary_scan)

    # Secondary Scan: alle 60 Min
    schedule.every(config.SECONDARY_SCAN_INTERVAL).minutes.do(run_secondary_scan)

    # Morning Briefing: 8:00
    schedule.every().day.at("08:00").do(morning_briefing)

    # EOD Summary: 22:00
    schedule.every().day.at("22:00").do(eod_summary)

    # Portfolio Check: 09:00 und 21:00
    schedule.every().day.at("09:00").do(portfolio_check_job)
    schedule.every().day.at("21:00").do(portfolio_check_job)

    # Weekly Deep Dive: Montag 08:30
    schedule.every().monday.at("08:30").do(weekly_deep_dive)

    # Weekly Review: Sonntag 20:00
    schedule.every().sunday.at("20:00").do(weekly_review)

    print("[Coordinator] Scheduler gestartet")
    print(f"  Primary Scan: alle {config.PRIMARY_SCAN_INTERVAL} Min (BTC, ETH)")
    print(f"  Secondary Scan: alle {config.SECONDARY_SCAN_INTERVAL} Min (SOL, DOGE, XRP)")
    print(f"  Morning Briefing: 08:00")
    print(f"  EOD Summary: 22:00")
    print(f"  Portfolio Check: 09:00, 21:00")
    print(f"  Weekly Deep Dive: Montag 08:30")
    print(f"  Weekly Review: Sonntag 20:00")

    while True:
        schedule.run_pending()
        time.sleep(30)


# ============================================================
# MAIN
# ============================================================

def main():
    # Create data directories
    for d in [config.DATA_DIR, config.LOG_DIR, config.SCAN_LOG_DIR,
              config.BACKTEST_CACHE_DIR]:
        os.makedirs(d, exist_ok=True)

    # Load saved chat ID
    chat_id_file = os.path.join(config.DATA_DIR, "chat_id.txt")
    if os.path.exists(chat_id_file):
        with open(chat_id_file, "r") as f:
            config.TELEGRAM_CHAT_ID = f.read().strip()

    print("🦀 Crypto Trading Agents v1.0")
    print(f"   Hyperliquid {'TESTNET' if config.HYPERLIQUID_TESTNET else 'MAINNET'}")
    print(f"   Pairs: {config.PRIMARY_PAIRS + config.SECONDARY_PAIRS}")
    print(f"   Timezone: {config.TIMEZONE}")
    print()

    # Start scheduler in background
    scheduler_thread = Thread(target=run_scheduler, daemon=True)
    scheduler_thread.start()

    # Start Telegram bot
    app = Application.builder().token(config.TELEGRAM_BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("scan", cmd_scan))
    app.add_handler(CommandHandler("scan_btc", cmd_scan_single))
    app.add_handler(CommandHandler("scan_eth", cmd_scan_single))
    app.add_handler(CommandHandler("scan_sol", cmd_scan_single))
    app.add_handler(CommandHandler("scan_doge", cmd_scan_single))
    app.add_handler(CommandHandler("scan_xrp", cmd_scan_single))
    app.add_handler(CommandHandler("status", cmd_status))
    app.add_handler(CommandHandler("research", cmd_research))
    app.add_handler(CommandHandler("backtest", cmd_backtest))
    app.add_handler(CommandHandler("risk", cmd_risk))
    app.add_handler(CommandHandler("briefing", cmd_briefing))
    app.add_handler(CommandHandler("summary", cmd_summary))
    app.add_handler(CommandHandler("weekly", cmd_weekly))
    app.add_handler(CommandHandler("alerts", cmd_alerts))
    app.add_handler(CommandHandler("help", cmd_help))
    app.add_handler(CommandHandler("positions", cmd_positions))
    app.add_handler(CommandHandler("trades", cmd_trades))
    app.add_handler(CommandHandler("close", cmd_close))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    print("[Coordinator] Telegram Bot gestartet. Sende /start an den Bot.")
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
