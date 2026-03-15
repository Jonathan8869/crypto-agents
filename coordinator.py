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
from auto_trader import (
    open_paper_trade, close_paper_trade, get_open_positions,
    update_positions_with_prices, format_status_message,
    get_available_cash, get_trade_history, get_portfolio_status
)
from polymarket_analyzer import PolymarketAnalyzer

# Initialize all agents
scanner = QuantScanner()
researcher = Researcher()
alert_agent = AlertAgent()
risk_manager = RiskManager()
backtest = BacktestAgent(scanner)
polymarket = PolymarketAnalyzer()

tz = pytz.timezone(config.TIMEZONE)

# Preis-Cache fuer /status
last_prices = {}


# ============================================================
# COORDINATOR LOGIC
# ============================================================

def get_price_from_result(result):
    """Extrahiert aktuellen Preis aus Scanner-Result."""
    tf_data = result.get("timeframe_data", {})
    for tf in ["15m", "1h", "4h"]:
        price = tf_data.get(tf, {}).get("levels", {}).get("current_price")
        if price:
            return float(price)
    return None


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
    """Verarbeite Scan-Ergebnisse: Trades immer ausfuehren, Backtest nur warnen."""
    global last_prices

    # Preise cachen & Positionen updaten
    current_prices = {}
    for result in results:
        symbol = result.get("symbol", "").replace("USDT", "").replace("PERP", "")
        price = get_price_from_result(result)
        if symbol and price:
            current_prices[symbol] = price

    if current_prices:
        last_prices.update(current_prices)
        update_positions_with_prices(current_prices)
        print(f"[Coordinator] Preise aktualisiert: {len(current_prices)} Coins")

    for result in results:
        level = result["confluence"]["level"]

        if level == "HIGH":
            # Backtest nur fuer Info
            setup_name = backtest.identify_setup(result)
            bt_result = backtest.backtest_signal(
                result["symbol"], setup_name, result.get("direction", "long")
            )
            if bt_result.get("win_rate", 0) < 40 and bt_result.get("trades", 0) >= 10:
                print(f"[WARN] {result['symbol']} Backtest schlecht ({bt_result.get('win_rate',0)}%), trade trotzdem!")

            evaluation = risk_manager.evaluate_trade(result)
            entry_price = get_price_from_result(result) or 0

            # Auto SL/TP wenn Risk Manager keine Werte liefert
            if entry_price > 0:
                risk_pct = 0.05
                reward_pct = 0.10
                if result.get("direction", "long") == "long":
                    auto_sl = entry_price * (1 - risk_pct)
                    auto_tp = entry_price * (1 + reward_pct)
                else:
                    auto_sl = entry_price * (1 + risk_pct)
                    auto_tp = entry_price * (1 - reward_pct)
                if evaluation.get("stop_loss", 0) == 0:
                    evaluation["stop_loss"] = auto_sl
                if evaluation.get("take_profit", 0) == 0:
                    evaluation["take_profit"] = auto_tp

            context = ""
            try:
                context = researcher.analyze_narrative_shift(f"{result['symbol']} {level} signal")
            except:
                pass

            # Immer traden bei HIGH Signal (kein approved-Check mehr)
            position_size_usd = evaluation.get("position_size_usd", 200.0)
            cash = get_available_cash()

            if cash < position_size_usd:
                print(f"[Coordinator] Nicht genug Cash! ${cash:.2f} < ${position_size_usd:.2f}")
                alert_agent.send_telegram(f"Trade uebersprungen: Nicht genug Cash (${cash:.2f})")
            elif entry_price == 0:
                print(f"[Coordinator] Kein Preis fuer {result['symbol']}")
            else:
                trade = open_paper_trade(
                    symbol=result["symbol"],
                    side=result.get("direction", "long").upper(),
                    entry_price=entry_price,
                    position_size_usd=position_size_usd,
                    stop_loss=evaluation.get("stop_loss", 0),
                    take_profit=evaluation.get("take_profit", 0),
                    confidence=level,
                    signal_data={},
                )
                if trade:
                    msg = alert_agent.format_signal_alert(result, context)
                    if bt_result.get("trades", 0) > 0:
                        msg += f"\n\nBacktest: {bt_result.get('win_rate',0)}% Win-Rate"
                    status = get_portfolio_status()
                    msg += (
                        f"\n\nAUTO TRADE EROEFFNET\n"
                        f"SL: ${trade['stop_loss']:.4f} | TP: ${trade['take_profit']:.4f}\n"
                        f"Cash danach: ${status['cash_balance']:,.2f}\n"
                        f"Offene Positionen: {status['open_positions']}"
                    )
                    alert_agent.send_telegram(msg)
                else:
                    alert_agent.send_telegram(f"Trade fehlgeschlagen: {result['symbol']}")


def morning_briefing():
    """Morning Briefing um 8:00."""
    now = datetime.now(tz)
    if now.weekday() >= 5:
        return
    results = run_full_scan()
    poly_signals = {}
    try:
        poly_signals = polymarket.get_sentiment_signals()
    except:
        pass
    research = researcher.daily_research(results, poly_signals)
    msg = alert_agent.format_morning_briefing(results, research)
    if msg:
        if poly_signals:
            pm_msg = polymarket.format_sentiment(poly_signals)
            msg += f"\n\n{pm_msg}"
        alert_agent.send_telegram(msg)


def daily_research_job():
    now = datetime.now(tz)
    if now.weekday() >= 5:
        return
    pass


def eod_summary():
    """End-of-Day Summary um 22:00."""
    results = run_full_scan()
    poly_signals = {}
    try:
        poly_signals = polymarket.get_sentiment_signals()
    except:
        pass
    research = researcher.daily_research(results, poly_signals)
    portfolio = risk_manager.portfolio
    msg = alert_agent.format_daily_summary(results, research, portfolio)
    if poly_signals:
        pm_msg = polymarket.format_sentiment(poly_signals)
        msg += f"\n\n{pm_msg}"
    status = get_portfolio_status(last_prices if last_prices else None)
    pnl_sign = "+" if status["total_pnl"] >= 0 else ""
    msg += (
        f"\n\nPORTFOLIO EOD\n"
        f"Gesamt-Wert: ${status['total_value']:,.2f}\n"
        f"P&L: ${pnl_sign}{status['total_pnl']:,.2f} ({pnl_sign}{status['total_return_pct']:.2f}%)\n"
        f"Cash: ${status['cash_balance']:,.2f}"
    )
    alert_agent.send_telegram(msg)


def portfolio_check_job():
    msg = risk_manager.portfolio_check()
    alert_agent.send_telegram(msg)


def weekly_deep_dive():
    results = run_full_scan()
    report = researcher.weekly_deep_dive(results)
    alert_agent.send_telegram(f"WOECHENTLICHER DEEP DIVE\n{report}")


def weekly_review():
    msg = alert_agent.format_weekly_review()
    alert_agent.send_telegram(msg)


# ============================================================
# TELEGRAM BOT COMMANDS
# ============================================================

async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    config.TELEGRAM_CHAT_ID = str(update.effective_chat.id)
    with open(os.path.join(config.DATA_DIR, "chat_id.txt"), "w") as f:
        f.write(config.TELEGRAM_CHAT_ID)
    await update.message.reply_text(
        "Crypto Trading Agents aktiv!\n\n"
        "Befehle:\n"
        "/scan - Alle Pairs scannen\n"
        "/status - Portfolio Status mit P&L\n"
        "/positions - Offene Positionen\n"
        "/trades - Trade Historie\n"
        "/close BTC - Position schliessen\n"
        "/research - Research Brief\n"
        "/backtest BTC - Backtest\n"
        "/risk - Risk Check\n"
        "/briefing - Morning Briefing\n"
        "/summary - Daily Summary\n"
        "/weekly - Weekly Deep Dive\n"
        "/alerts - Letzte Alerts\n"
        "/help - Alle Befehle"
    )


async def cmd_scan(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Scanne alle Pairs...")
    loop = asyncio.get_event_loop()
    results = await loop.run_in_executor(None, run_full_scan)
    signals = [r for r in results if r["confluence"]["level"] not in ["NONE", "WATCH"]]
    if signals:
        for r in signals:
            msg = alert_agent.format_signal_alert(r)
            await update.message.reply_text(msg)
    else:
        await update.message.reply_text("Keine aktiven Signals. Markt ruhig.")


async def cmd_scan_single(update: Update, context: ContextTypes.DEFAULT_TYPE):
    cmd = update.message.text.split("_")
    if len(cmd) < 2:
        await update.message.reply_text("Nutze: /scan_btc, /scan_eth, /scan_sol")
        return
    symbol = cmd[1].upper()
    if symbol not in config.PRIMARY_PAIRS + config.SECONDARY_PAIRS:
        await update.message.reply_text(f"{symbol} nicht in der Watchlist")
        return
    await update.message.reply_text(f"Scanne {symbol}...")
    result = scanner.scan_pair(symbol)
    msg = alert_agent.format_signal_alert(result)
    await update.message.reply_text(msg)


async def cmd_status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Portfolio Status mit echtem P&L."""
    try:
        status = get_portfolio_status(last_prices if last_prices else None)
        pnl = status["total_pnl"]
        s = "+" if pnl >= 0 else ""
        msg1 = (
            "PAPER TRADING STATUS\n"
            "=======================\n"
            f"Cash:          ${status['cash_balance']:>10,.2f}\n"
            f"Positionen:    ${status['positions_value']:>10,.2f}\n"
            f"Gesamt-Wert:   ${status['total_value']:>10,.2f}\n"
            "=======================\n"
            f"Unrealized P&L: ${s}{status['unrealized_pnl']:,.2f}\n"
            f"Realized P&L:   ${s}{status['realized_pnl']:,.2f}\n"
            f"Total P&L:      ${s}{pnl:,.2f} ({s}{status['total_return_pct']:.2f}%)\n"
            "=======================\n"
            f"Offene Pos.:   {status['open_positions']}\n"
            f"Trades gesamt: {status['total_trades']}\n"
            f"Win-Rate:      {status['win_rate']:.1f}%"
        )
        await update.message.reply_text(msg1)
        if status["positions"]:
            lines = ["OFFENE POSITIONEN:"]
            for pos in status["positions"][:10]:
                e = "+" if pos["unrealized_pnl"] >= 0 else "-"
                lines.append(f"[{e}] {pos['symbol']}: ${pos['current_price']:.4f} | P&L: ${pos['unrealized_pnl']:+.2f} ({pos['unrealized_pnl_pct']:+.2f}%)")
            await update.message.reply_text("\n".join(lines))
    except Exception as e:
        await update.message.reply_text(f"Status-Fehler: {e}")


async def cmd_research(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Generiere Research Brief...")
    results = scanner.scan_all(config.PRIMARY_PAIRS)
    report = researcher.daily_research(results)
    await update.message.reply_text(f"RESEARCH BRIEF\n{report}")


async def cmd_backtest(update: Update, context: ContextTypes.DEFAULT_TYPE):
    args = context.args
    if not args:
        await update.message.reply_text("Nutze: /backtest BTC")
        return
    symbol = args[0].upper()
    await update.message.reply_text(f"Backteste {symbol}...")
    result = scanner.scan_pair(symbol)
    setup_name = backtest.identify_setup(result)
    if setup_name == "no_setup":
        await update.message.reply_text(f"Kein aktives Setup fuer {symbol}.")
        return
    bt_result = backtest.backtest_signal(symbol, setup_name, result.get("direction", "long"))
    msg = backtest.format_backtest(bt_result)
    await update.message.reply_text(msg)


async def cmd_risk(update: Update, context: ContextTypes.DEFAULT_TYPE):
    results = scanner.scan_all(config.PRIMARY_PAIRS)
    high_signals = [r for r in results if r["confluence"]["level"] in ["HIGH", "MEDIUM"]]
    if not high_signals:
        await update.message.reply_text("Keine aktiven Signals fuer Risk Check.")
        return
    for result in high_signals:
        evaluation = risk_manager.evaluate_trade(result)
        msg = risk_manager.format_evaluation(evaluation)
        await update.message.reply_text(msg)


async def cmd_briefing(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Generiere Briefing...")
    loop = asyncio.get_event_loop()
    results = await loop.run_in_executor(None, run_full_scan)
    research = researcher.daily_research(results)
    msg = alert_agent.format_morning_briefing(results, research)
    if msg:
        await update.message.reply_text(msg)
    else:
        await update.message.reply_text("Keine relevanten Veraenderungen.")


async def cmd_summary(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Generiere Summary...")
    loop = asyncio.get_event_loop()
    results = await loop.run_in_executor(None, run_full_scan)
    research = researcher.daily_research(results)
    portfolio = risk_manager.portfolio
    msg = alert_agent.format_daily_summary(results, research, portfolio)
    await update.message.reply_text(msg)


async def cmd_weekly(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Generiere Weekly Deep Dive...")
    loop = asyncio.get_event_loop()
    results = await loop.run_in_executor(None, run_full_scan)
    report = researcher.weekly_deep_dive(results)
    await update.message.reply_text(f"WEEKLY DEEP DIVE\n{report}")


async def cmd_alerts(update: Update, context: ContextTypes.DEFAULT_TYPE):
    recent = alert_agent.alert_history[-10:]
    if not recent:
        await update.message.reply_text("Keine Alerts bisher.")
        return
    msg = "LETZTE ALERTS:\n"
    for a in reversed(recent):
        t = datetime.fromisoformat(a["time"]).strftime("%d.%m %H:%M")
        msg += f"{t} | {a['symbol']} | {a['level']} | {a.get('direction', '?')}\n"
    await update.message.reply_text(msg)


async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "CRYPTO AGENTS - BEFEHLE\n"
        "========================\n"
        "SCANNER\n"
        "  /scan - Alle Pairs scannen\n"
        "  /scan_btc /scan_eth /scan_sol\n\n"
        "RESEARCH\n"
        "  /research - Research Brief\n"
        "  /weekly - Weekly Deep Dive\n\n"
        "RISK & STATUS\n"
        "  /status - Portfolio + P&L\n"
        "  /risk - Risk Check\n\n"
        "BACKTEST\n"
        "  /backtest BTC\n\n"
        "TRADING\n"
        "  /positions - Offene Positionen\n"
        "  /trades - Trade Historie\n"
        "  /close BTC - Position schliessen\n\n"
        "POLYMARKET\n"
        "  /poly /poly_crypto /poly_macro\n\n"
        "REPORTS\n"
        "  /briefing /summary /alerts\n"
    )


async def cmd_positions(update: Update, context: ContextTypes.DEFAULT_TYPE):
    positions = get_open_positions()
    cash = get_available_cash()
    if not positions:
        await update.message.reply_text(f"Keine offenen Positionen\nCash: ${cash:,.2f}")
        return
    lines = [f"OFFENE POSITIONEN ({len(positions)})\n"]
    for symbol, pos in positions.items():
        pnl = pos.get("unrealized_pnl", 0)
        pct = pos.get("unrealized_pnl_pct", 0)
        cur_price = pos.get("current_price", pos["entry_price"])
        lines.append(
            f"{symbol} {pos['side']}\n"
            f"  Entry: ${pos['entry_price']:.4f} -> Aktuell: ${cur_price:.4f}\n"
            f"  P&L: ${pnl:+.2f} ({pct:+.2f}%)\n"
        )
    lines.append(f"Cash: ${cash:,.2f}")
    await update.message.reply_text("\n".join(lines))


async def cmd_trades(update: Update, context: ContextTypes.DEFAULT_TYPE):
    history = get_trade_history(limit=10)
    if not history:
        await update.message.reply_text("Noch keine abgeschlossenen Trades.")
        return
    lines = ["LETZTE TRADES:\n"]
    for t in history:
        pnl = t.get("realized_pnl", 0)
        pct = t.get("realized_pnl_pct", 0)
        emoji = "+" if pnl >= 0 else "-"
        closed_at = t.get("closed_at", "")[:16].replace("T", " ")
        lines.append(f"[{emoji}] {t['symbol']} {t['side']} | P&L: ${pnl:+.2f} ({pct:+.2f}%) | {t.get('close_reason', '?')} | {closed_at}")
    status = get_portfolio_status()
    lines.append(f"\nRealized P&L gesamt: ${status['realized_pnl']:+.2f}")
    lines.append(f"Win-Rate: {status['win_rate']:.1f}%")
    await update.message.reply_text("\n".join(lines))


async def cmd_close(update: Update, context: ContextTypes.DEFAULT_TYPE):
    args = context.args
    if not args:
        await update.message.reply_text("Nutze: /close BTC")
        return
    symbol = args[0].upper()
    positions = get_open_positions()
    if symbol not in positions:
        await update.message.reply_text(f"Keine offene Position fuer {symbol}")
        return
    pos = positions[symbol]
    exit_price = last_prices.get(symbol) or pos.get("current_price") or pos["entry_price"]
    await update.message.reply_text(f"Schliesse {symbol} @ ${exit_price:.4f}...")
    closed = close_paper_trade(symbol, exit_price, "MANUAL")
    if closed:
        pnl = closed["realized_pnl"]
        pct = closed["realized_pnl_pct"]
        cash = get_available_cash()
        await update.message.reply_text(
            f"{symbol} geschlossen\n"
            f"Exit: ${exit_price:.4f}\n"
            f"P&L: ${pnl:+.2f} ({pct:+.2f}%)\n"
            f"Cash jetzt: ${cash:,.2f}"
        )
    else:
        await update.message.reply_text(f"Fehler beim Schliessen von {symbol}")


async def cmd_polymarket(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Lade Polymarket Daten...")
    try:
        signals = polymarket.get_sentiment_signals()
        msg = polymarket.format_sentiment(signals)
        await update.message.reply_text(msg)
    except Exception as e:
        await update.message.reply_text(f"Fehler: {e}")


async def cmd_polymarket_crypto(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Lade Crypto Markets...")
    try:
        markets = polymarket.get_crypto_markets()
        msg = polymarket.format_market_summary(markets, "CRYPTO PREDICTION MARKETS")
        await update.message.reply_text(msg)
    except Exception as e:
        await update.message.reply_text(f"Fehler: {e}")


async def cmd_polymarket_macro(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Lade Macro Markets...")
    try:
        markets = polymarket.get_macro_markets()
        msg = polymarket.format_market_summary(markets, "MACRO PREDICTION MARKETS")
        await update.message.reply_text(msg)
    except Exception as e:
        await update.message.reply_text(f"Fehler: {e}")


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Normale Nachrichten mit Kimi K2.5 beantworten."""
    user_msg = update.message.text
    if not user_msg:
        return

    status = get_portfolio_status(last_prices if last_prices else None)
    positions = status.get("positions", [])

    positions_str = ""
    for p in positions:
        positions_str += f"- {p['symbol']} {p['side']} @ ${p['entry_price']:,.2f} | P&L: ${p['unrealized_pnl']:+.2f}\n"

    recent_alerts = alert_agent.alert_history[-5:]
    alerts_str = ""
    for a in recent_alerts:
        alerts_str += f"- {a['symbol']} {a['level']} {a.get('direction','?')} ({a['time'][:16]})\n"

    system_prompt = f"""Du bist ein Krypto-Trading Assistent. Du sprichst Deutsch.
Du hast Zugriff auf folgende Live-Daten:

PORTFOLIO:
Gesamt-Wert: ${status['total_value']:,.2f}
Cash: ${status['cash_balance']:,.2f}
Unrealized P&L: ${status['unrealized_pnl']:+.2f}
Realized P&L: ${status['realized_pnl']:+.2f}
Total Return: {status['total_return_pct']:+.2f}%

OFFENE POSITIONEN:
{positions_str if positions_str else 'Keine'}

LETZTE ALERTS:
{alerts_str if alerts_str else 'Keine'}

REGELN:
- Antworte kurz und praezise (max 200 Woerter)
- Gib keine Finanzberatung, nur Analyse
- Wenn gefragt nach Scans/Trades, verweise auf die Befehle
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
    schedule.every(config.PRIMARY_SCAN_INTERVAL).minutes.do(run_primary_scan)
    schedule.every(config.SECONDARY_SCAN_INTERVAL).minutes.do(run_secondary_scan)
    schedule.every().day.at("08:00").do(morning_briefing)
    schedule.every().day.at("22:00").do(eod_summary)
    schedule.every().day.at("09:00").do(portfolio_check_job)
    schedule.every().day.at("21:00").do(portfolio_check_job)
    schedule.every().monday.at("08:30").do(weekly_deep_dive)
    schedule.every().sunday.at("20:00").do(weekly_review)

    print("[Coordinator] Scheduler gestartet")
    print(f"  Primary Scan: alle {config.PRIMARY_SCAN_INTERVAL} Min (BTC, ETH)")
    print(f"  Secondary Scan: alle {config.SECONDARY_SCAN_INTERVAL} Min (SOL, DOGE, XRP)")
    print(f"  Morning Briefing: 08:00")
    print(f"  EOD Summary: 22:00")

    while True:
        schedule.run_pending()
        time.sleep(30)


# ============================================================
# MAIN
# ============================================================

def main():
    for d in [config.DATA_DIR, config.LOG_DIR, config.SCAN_LOG_DIR,
              config.BACKTEST_CACHE_DIR]:
        os.makedirs(d, exist_ok=True)

    chat_id_file = os.path.join(config.DATA_DIR, "chat_id.txt")
    if os.path.exists(chat_id_file):
        with open(chat_id_file, "r") as f:
            config.TELEGRAM_CHAT_ID = f.read().strip()

    print("Crypto Trading Agents v1.0")
    print(f"   Hyperliquid {'TESTNET' if config.HYPERLIQUID_TESTNET else 'MAINNET'}")
    print(f"   Pairs: {config.PRIMARY_PAIRS + config.SECONDARY_PAIRS}")
    print(f"   Timezone: {config.TIMEZONE}")
    print()

    scheduler_thread = Thread(target=run_scheduler, daemon=True)
    scheduler_thread.start()

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
    app.add_handler(CommandHandler("poly", cmd_polymarket))
    app.add_handler(CommandHandler("poly_crypto", cmd_polymarket_crypto))
    app.add_handler(CommandHandler("poly_macro", cmd_polymarket_macro))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    print("[Coordinator] Telegram Bot gestartet. Sende /start an den Bot.")
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
