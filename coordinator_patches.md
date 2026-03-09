# coordinator.py – Patches für Portfolio-Fix

## 1. Import ersetzen (oben in der Datei)

**ALT (irgendwas mit auto_trader):**
```python
from auto_trader import open_paper_trade, close_paper_trade, get_open_positions
```

**NEU:**
```python
from auto_trader import (
    open_paper_trade, close_paper_trade, get_open_positions,
    update_positions_with_prices, get_portfolio_status,
    format_status_message, get_available_cash, get_trade_history
)
```

---

## 2. Nach jedem Scanner-Durchlauf: Preise updaten

In der Funktion die den Scan ausführt (z.B. `run_scan()` oder `scheduled_scan()`),
**nach** dem Scanner-Aufruf folgendes einfügen:

```python
# --- NEU: Positionen mit aktuellen Preisen updaten ---
if scan_results:
    current_prices = {}
    for result in scan_results:
        symbol = result.get("symbol", "").replace("USDT", "").replace("PERP", "")
        price = result.get("current_price") or result.get("price")
        if symbol and price:
            current_prices[symbol] = price
    
    if current_prices:
        from auto_trader import update_positions_with_prices
        updated = update_positions_with_prices(current_prices)
        open_pos = updated.get("positions", {})
        if open_pos:
            logger.info(f"Portfolio aktualisiert: {len(open_pos)} offene Positionen")
# --- ENDE NEU ---
```

---

## 3. /status Command fixen

**ALT (zeigt nur statische Info):**
```python
@dp.message(Command("status"))
async def cmd_status(message: Message):
    # ... alte Implementierung ohne P&L
    await message.answer("Portfolio: $10,000 ...")
```

**NEU:**
```python
@dp.message(Command("status"))
async def cmd_status(message: Message):
    try:
        # Aktuelle Preise holen (aus letztem Scan-Cache oder neu)
        current_prices = getattr(coordinator_instance, 'last_prices', None) or {}
        status_text = format_status_message(current_prices if current_prices else None)
        await message.answer(status_text, parse_mode="Markdown")
    except Exception as e:
        logger.error(f"/status Fehler: {e}")
        await message.answer(f"❌ Status-Fehler: {e}")
```

---

## 4. /positions Command (neu oder fixen)

```python
@dp.message(Command("positions"))
async def cmd_positions(message: Message):
    positions = get_open_positions()
    cash = get_available_cash()
    
    if not positions:
        await message.answer(f"📭 Keine offenen Positionen\n💵 Cash: ${cash:,.2f}")
        return
    
    lines = [f"📊 **OFFENE POSITIONEN** ({len(positions)})\n"]
    for symbol, pos in positions.items():
        pnl = pos.get('unrealized_pnl', 0)
        pct = pos.get('unrealized_pnl_pct', 0)
        emoji = "🟢" if pos['side'] == "LONG" else "🔴"
        lines.append(
            f"{emoji} **{symbol}** {pos['side']}\n"
            f"   Entry: ${pos['entry_price']:.4f} → Aktuell: ${pos.get('current_price', pos['entry_price']):.4f}\n"
            f"   Qty: {pos['quantity']:.6f} | Size: ${pos['position_size_usd']:.2f}\n"
            f"   P&L: ${pnl:+.2f} ({pct:+.2f}%)\n"
        )
    lines.append(f"💵 Cash: ${cash:,.2f}")
    await message.answer("\n".join(lines), parse_mode="Markdown")
```

---

## 5. /trades Command (letzte abgeschlossene Trades)

```python
@dp.message(Command("trades"))
async def cmd_trades(message: Message):
    history = get_trade_history(limit=10)
    if not history:
        await message.answer("📭 Noch keine abgeschlossenen Trades")
        return
    
    lines = ["📜 **LETZTE TRADES:**\n"]
    for t in history:
        pnl = t.get('realized_pnl', 0)
        emoji = "✅" if pnl >= 0 else "❌"
        lines.append(
            f"{emoji} {t['symbol']} {t['side']} | "
            f"P&L: ${pnl:+.2f} ({t.get('realized_pnl_pct', 0):+.2f}%) | "
            f"{t.get('close_reason', '?')}"
        )
    await message.answer("\n".join(lines), parse_mode="Markdown")
```

---

## 6. Wenn ein Trade-Signal zu einem Kauf führt

**ALT (führt Trade aus aber aktualisiert Portfolio nicht):**
```python
# irgendwas mit create_order oder trade logging
trade_result = some_trade_function(...)
```

**NEU:**
```python
# Position Size vom Risk Manager holen
position_size_usd = risk_data.get("position_size_usd", 200.0)
cash = get_available_cash()

if cash < position_size_usd:
    logger.warning(f"Nicht genug Cash! ${cash:.2f} < ${position_size_usd:.2f}")
else:
    trade = open_paper_trade(
        symbol=signal["symbol"],
        side=signal["direction"],       # "LONG" oder "SHORT"
        entry_price=signal["price"],
        position_size_usd=position_size_usd,
        stop_loss=risk_data["stop_loss"],
        take_profit=risk_data["take_profit"],
        confidence=signal["confluence"],
        signal_data=signal,
    )
    if trade:
        logger.info(f"Trade geöffnet: {trade}")
```

---

## 7. Preise im Coordinator cachen (für /status)

In der Klasse oder am Anfang des Coordinators:

```python
# Preis-Cache für /status
self.last_prices = {}  # oder global last_prices = {}
```

Im Scanner-Callback:
```python
# Preise cachen
for result in scan_results:
    sym = result.get("symbol", "").replace("USDT", "")
    price = result.get("current_price") or result.get("price")
    if sym and price:
        self.last_prices[sym] = price  # oder last_prices[sym] = price
```

---

## Deployment auf VPS

```bash
# 1. Datei hochladen
scp auto_trader.py root@<VPS-IP>:/tmp/auto_trader.py

# 2. In Container kopieren
docker cp /tmp/auto_trader.py openclaw-w8sy-openclaw-1:/data/crypto-agents/auto_trader.py

# 3. Alte Portfolio-Datei backup (falls vorhanden)
docker exec openclaw-w8sy-openclaw-1 cp /data/crypto-agents/portfolio.json /data/crypto-agents/portfolio.json.bak 2>/dev/null || true

# 4. Neu starten
docker exec -it openclaw-w8sy-openclaw-1 bash -c "
  cd /data/crypto-agents &&
  pkill -9 -f python3 2>/dev/null
  sleep 2
  python3 coordinator.py > /data/crypto-agents/logs/coordinator.log 2>&1 &
  echo 'Started!'
"

# 5. Logs prüfen
docker exec openclaw-w8sy-openclaw-1 tail -f /data/crypto-agents/logs/coordinator.log
```
