"""
auto_trader.py - Paper Trading mit echtem Portfolio-Tracking
Fix: Portfolio wird jetzt korrekt aktualisiert, P&L getrackt, Testgeld reduziert/erhöht
"""

import json
import os
import logging
from datetime import datetime, timezone
from typing import Optional

logger = logging.getLogger(__name__)

# Portfolio-Datei für Persistenz
PORTFOLIO_FILE = "/data/crypto-agents/portfolio.json"
INITIAL_BALANCE = 10000.0  # $10.000 Testgeld


# ─────────────────────────────────────────────
#  Portfolio laden / speichern
# ─────────────────────────────────────────────

def _load_portfolio() -> dict:
    """Lädt Portfolio aus JSON. Erstellt neues wenn nicht vorhanden."""
    if os.path.exists(PORTFOLIO_FILE):
        try:
            with open(PORTFOLIO_FILE, "r") as f:
                data = json.load(f)
            # Migration: alte Struktur ohne cash_balance
            if "cash_balance" not in data:
                data["cash_balance"] = INITIAL_BALANCE
            return data
        except Exception as e:
            logger.error(f"Portfolio laden fehlgeschlagen: {e}")

    return {
        "cash_balance": INITIAL_BALANCE,
        "initial_balance": INITIAL_BALANCE,
        "positions": {},       # symbol → position dict
        "closed_trades": [],   # Liste aller geschlossenen Trades
        "total_realized_pnl": 0.0,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "last_updated": datetime.now(timezone.utc).isoformat(),
    }


def _save_portfolio(portfolio: dict):
    """Speichert Portfolio in JSON."""
    portfolio["last_updated"] = datetime.now(timezone.utc).isoformat()
    os.makedirs(os.path.dirname(PORTFOLIO_FILE), exist_ok=True)
    try:
        with open(PORTFOLIO_FILE, "w") as f:
            json.dump(portfolio, f, indent=2)
    except Exception as e:
        logger.error(f"Portfolio speichern fehlgeschlagen: {e}")


# ─────────────────────────────────────────────
#  KAUF - Testgeld wird reduziert
# ─────────────────────────────────────────────

def open_paper_trade(symbol: str, side: str, entry_price: float,
                     position_size_usd: float, stop_loss: float,
                     take_profit: float, confidence: str = "MEDIUM",
                     signal_data: dict = None) -> dict:
    """
    Öffnet eine Paper-Trade Position.
    Reduziert cash_balance um position_size_usd.
    
    Returns: Trade-Dict mit allen Details, oder None bei Fehler
    """
    portfolio = _load_portfolio()

    # Prüfungen
    if symbol in portfolio["positions"]:
        logger.warning(f"Position für {symbol} bereits offen – überspringe")
        return None

    if position_size_usd <= 0 or entry_price <= 0:
        logger.error(f"Ungültige Werte: size={position_size_usd}, price={entry_price}")
        return None

    if portfolio["cash_balance"] < position_size_usd:
        logger.warning(
            f"Nicht genug Cash! Verfügbar: ${portfolio['cash_balance']:.2f}, "
            f"Benötigt: ${position_size_usd:.2f}"
        )
        return None

    # Quantity berechnen
    quantity = position_size_usd / entry_price

    # Trade-Objekt erstellen
    trade = {
        "symbol": symbol,
        "side": side.upper(),
        "entry_price": entry_price,
        "quantity": quantity,
        "position_size_usd": position_size_usd,
        "stop_loss": stop_loss,
        "take_profit": take_profit,
        "confidence": confidence,
        "signal_data": signal_data or {},
        "current_price": entry_price,
        "unrealized_pnl": 0.0,
        "unrealized_pnl_pct": 0.0,
        "opened_at": datetime.now(timezone.utc).isoformat(),
        "status": "OPEN",
    }

    # Portfolio aktualisieren
    portfolio["positions"][symbol] = trade
    portfolio["cash_balance"] -= position_size_usd
    portfolio["cash_balance"] = round(portfolio["cash_balance"], 2)

    _save_portfolio(portfolio)

    logger.info(
        f"📈 PAPER TRADE ERÖFFNET: {side.upper()} {symbol} | "
        f"Entry: ${entry_price:.4f} | Qty: {quantity:.6f} | "
        f"Size: ${position_size_usd:.2f} | "
        f"Cash danach: ${portfolio['cash_balance']:.2f}"
    )
    return trade


# ─────────────────────────────────────────────
#  VERKAUF - Testgeld wird erhöht + P&L gebucht
# ─────────────────────────────────────────────

def close_paper_trade(symbol: str, exit_price: float, reason: str = "MANUAL") -> dict:
    """
    Schließt eine offene Position.
    Erhöht cash_balance um exit_value, bucht P&L.
    
    Returns: Abgeschlossener Trade mit P&L-Details
    """
    portfolio = _load_portfolio()

    if symbol not in portfolio["positions"]:
        logger.warning(f"Keine offene Position für {symbol}")
        return None

    trade = portfolio["positions"][symbol]
    entry_price = trade["entry_price"]
    quantity = trade["quantity"]
    side = trade["side"]

    # P&L berechnen
    if side == "LONG":
        pnl = (exit_price - entry_price) * quantity
    else:  # SHORT
        pnl = (entry_price - exit_price) * quantity

    pnl_pct = (pnl / trade["position_size_usd"]) * 100
    exit_value = trade["position_size_usd"] + pnl  # Was wir zurückbekommen

    # Abgeschlossenen Trade archivieren
    closed_trade = {
        **trade,
        "exit_price": exit_price,
        "exit_value_usd": round(exit_value, 2),
        "realized_pnl": round(pnl, 2),
        "realized_pnl_pct": round(pnl_pct, 2),
        "closed_at": datetime.now(timezone.utc).isoformat(),
        "close_reason": reason,
        "status": "CLOSED",
    }

    # Portfolio aktualisieren
    del portfolio["positions"][symbol]
    portfolio["cash_balance"] += exit_value
    portfolio["cash_balance"] = round(portfolio["cash_balance"], 2)
    portfolio["total_realized_pnl"] += pnl
    portfolio["total_realized_pnl"] = round(portfolio["total_realized_pnl"], 2)
    portfolio["closed_trades"].append(closed_trade)

    _save_portfolio(portfolio)

    emoji = "✅" if pnl >= 0 else "❌"
    logger.info(
        f"{emoji} PAPER TRADE GESCHLOSSEN: {symbol} | "
        f"Exit: ${exit_price:.4f} | P&L: ${pnl:+.2f} ({pnl_pct:+.2f}%) | "
        f"Grund: {reason} | Cash danach: ${portfolio['cash_balance']:.2f}"
    )
    return closed_trade


# ─────────────────────────────────────────────
#  POSITIONEN UPDATEN (bei jedem Scan aufrufen)
# ─────────────────────────────────────────────

def update_positions_with_prices(current_prices: dict) -> dict:
    """
    Aktualisiert alle offenen Positionen mit aktuellen Preisen.
    Berechnet unrealized P&L für jede Position.
    
    Aufruf: Bei jedem Scanner-Durchlauf mit aktuellem price dict.
    current_prices: {"BTC": 67420.0, "ETH": 3240.0, ...}
    
    Returns: Updated portfolio dict
    """
    portfolio = _load_portfolio()

    if not portfolio["positions"]:
        return portfolio

    auto_closed = []

    for symbol, trade in list(portfolio["positions"].items()):
        # Symbol normalisieren (BTCUSDT → BTC)
        base_symbol = symbol.replace("USDT", "").replace("PERP", "")
        price = current_prices.get(base_symbol) or current_prices.get(symbol)

        if not price:
            logger.debug(f"Kein Preis für {symbol} gefunden")
            continue

        entry_price = trade["entry_price"]
        quantity = trade["quantity"]
        side = trade["side"]

        # Unrealized P&L berechnen
        if side == "LONG":
            pnl = (price - entry_price) * quantity
        else:
            pnl = (entry_price - price) * quantity

        pnl_pct = (pnl / trade["position_size_usd"]) * 100

        trade["current_price"] = price
        trade["unrealized_pnl"] = round(pnl, 2)
        trade["unrealized_pnl_pct"] = round(pnl_pct, 2)

        # Stop-Loss / Take-Profit Checks
        stop_loss = trade.get("stop_loss", 0)
        take_profit = trade.get("take_profit", 0)

        if side == "LONG":
            if stop_loss and price <= stop_loss:
                auto_closed.append((symbol, price, "STOP_LOSS"))
            elif take_profit and price >= take_profit:
                auto_closed.append((symbol, price, "TAKE_PROFIT"))
        else:
            if stop_loss and price >= stop_loss:
                auto_closed.append((symbol, price, "STOP_LOSS"))
            elif take_profit and price <= take_profit:
                auto_closed.append((symbol, price, "TAKE_PROFIT"))

    _save_portfolio(portfolio)

    # Auto-Close ausführen (nach dem Loop, um dict-Mutation zu vermeiden)
    for symbol, price, reason in auto_closed:
        logger.info(f"🤖 AUTO-CLOSE {symbol} wegen {reason} @ ${price:.4f}")
        close_paper_trade(symbol, price, reason)

    return _load_portfolio()  # Neu laden nach möglichen Closes


# ─────────────────────────────────────────────
#  STATUS - Für /status Befehl
# ─────────────────────────────────────────────

def get_portfolio_status(current_prices: dict = None) -> dict:
    """
    Gibt vollständigen Portfolio-Status zurück.
    Optionally updated mit aktuellen Preisen.
    
    Returns dict mit:
    - cash_balance: Verfügbares Cash
    - positions_value: Wert aller offenen Positionen
    - total_value: Gesamt-Portfolio-Wert
    - unrealized_pnl: Nicht realisierter P&L
    - realized_pnl: Realisierter P&L
    - total_pnl: Gesamt P&L
    - total_return_pct: Return in %
    - positions: Liste der offenen Positionen
    """
    if current_prices:
        portfolio = update_positions_with_prices(current_prices)
    else:
        portfolio = _load_portfolio()

    cash = portfolio["cash_balance"]
    initial = portfolio.get("initial_balance", INITIAL_BALANCE)
    positions = portfolio.get("positions", {})
    realized_pnl = portfolio.get("total_realized_pnl", 0.0)

    # Positions-Wert & unrealized P&L berechnen
    positions_value = 0.0
    unrealized_pnl = 0.0
    positions_list = []

    for symbol, trade in positions.items():
        cur_price = trade.get("current_price", trade["entry_price"])
        pos_value = cur_price * trade["quantity"]
        positions_value += pos_value
        unrealized_pnl += trade.get("unrealized_pnl", 0.0)
        positions_list.append({
            "symbol": symbol,
            "side": trade["side"],
            "entry_price": trade["entry_price"],
            "current_price": cur_price,
            "quantity": trade["quantity"],
            "position_size_usd": trade["position_size_usd"],
            "current_value_usd": round(pos_value, 2),
            "unrealized_pnl": trade.get("unrealized_pnl", 0.0),
            "unrealized_pnl_pct": trade.get("unrealized_pnl_pct", 0.0),
        })

    total_value = cash + positions_value
    total_pnl = realized_pnl + unrealized_pnl
    total_return_pct = ((total_value - initial) / initial) * 100

    # Win-Rate berechnen
    closed = portfolio.get("closed_trades", [])
    wins = sum(1 for t in closed if t.get("realized_pnl", 0) > 0)
    win_rate = (wins / len(closed) * 100) if closed else 0.0

    return {
        "cash_balance": round(cash, 2),
        "positions_value": round(positions_value, 2),
        "total_value": round(total_value, 2),
        "unrealized_pnl": round(unrealized_pnl, 2),
        "realized_pnl": round(realized_pnl, 2),
        "total_pnl": round(total_pnl, 2),
        "total_return_pct": round(total_return_pct, 2),
        "initial_balance": initial,
        "open_positions": len(positions),
        "total_trades": len(closed),
        "win_rate": round(win_rate, 1),
        "positions": sorted(positions_list, key=lambda x: abs(x["unrealized_pnl"]), reverse=True),
    }


def format_status_message(current_prices: dict = None) -> str:
    """Formatiert /status als Telegram-Nachricht."""
    s = get_portfolio_status(current_prices)

    pnl_emoji = "📈" if s["total_pnl"] >= 0 else "📉"
    pnl_sign = "+" if s["total_pnl"] >= 0 else ""
    ret_sign = "+" if s["total_return_pct"] >= 0 else ""

    lines = [
        f"💼 **PAPER TRADING STATUS**",
        f"━━━━━━━━━━━━━━━━━━━━━━━",
        f"💵 Cash:          ${s['cash_balance']:>10,.2f}",
        f"📊 Positionen:    ${s['positions_value']:>10,.2f}",
        f"🏦 Gesamt-Wert:   ${s['total_value']:>10,.2f}",
        f"━━━━━━━━━━━━━━━━━━━━━━━",
        f"{pnl_emoji} Unrealized P&L:  ${pnl_sign}{s['unrealized_pnl']:,.2f}",
        f"✅ Realized P&L:   ${pnl_sign}{s['realized_pnl']:,.2f}",
        f"📈 Total P&L:      ${pnl_sign}{s['total_pnl']:,.2f} ({ret_sign}{s['total_return_pct']:.2f}%)",
        f"━━━━━━━━━━━━━━━━━━━━━━━",
        f"📋 Offene Pos.:   {s['open_positions']}",
        f"🔢 Trades gesamt: {s['total_trades']}",
        f"🎯 Win-Rate:      {s['win_rate']:.1f}%",
    ]

    if s["positions"]:
        lines.append(f"\n📌 **OFFENE POSITIONEN:**")
        for pos in s["positions"]:
            side_emoji = "🟢" if pos["side"] == "LONG" else "🔴"
            pnl_str = f"{'+' if pos['unrealized_pnl'] >= 0 else ''}{pos['unrealized_pnl']:.2f}"
            pct_str = f"{'+' if pos['unrealized_pnl_pct'] >= 0 else ''}{pos['unrealized_pnl_pct']:.2f}%"
            lines.append(
                f"{side_emoji} {pos['symbol']}: ${pos['current_price']:.4f} | "
                f"P&L: ${pnl_str} ({pct_str})"
            )

    return "\n".join(lines)


# ─────────────────────────────────────────────
#  HELPER - Für coordinator.py
# ─────────────────────────────────────────────

def get_open_positions() -> dict:
    """Gibt alle offenen Positionen zurück."""
    return _load_portfolio().get("positions", {})


def get_available_cash() -> float:
    """Gibt verfügbares Cash zurück."""
    return _load_portfolio().get("cash_balance", 0.0)


def get_trade_history(limit: int = 20) -> list:
    """Gibt letzte N geschlossene Trades zurück."""
    portfolio = _load_portfolio()
    trades = portfolio.get("closed_trades", [])
    return sorted(trades, key=lambda x: x.get("closed_at", ""), reverse=True)[:limit]


def reset_portfolio():
    """Setzt Portfolio auf $10.000 zurück (nur für Tests!)."""
    portfolio = {
        "cash_balance": INITIAL_BALANCE,
        "initial_balance": INITIAL_BALANCE,
        "positions": {},
        "closed_trades": [],
        "total_realized_pnl": 0.0,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "last_updated": datetime.now(timezone.utc).isoformat(),
    }
    _save_portfolio(portfolio)
    logger.info(f"Portfolio zurückgesetzt auf ${INITIAL_BALANCE:.2f}")
    return portfolio
