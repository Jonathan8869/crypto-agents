"""
Agent 3: The Researcher
Krypto Research Analyst - tägliches Intel, News, Narrative, Macro.
Nutzt Kimi K2.5 via Ollama für Analyse.
"""

import json
import os
import requests
from datetime import datetime, timezone
import config


class Researcher:
    def __init__(self):
        self.ollama_url = f"http://172.17.0.1:32768/v1/chat/completions"
        self.model = "kimi-k2.5:cloud"
        os.makedirs(config.LOG_DIR, exist_ok=True)

    def _ask_kimi(self, prompt: str, max_tokens: int = 800) -> str:
        try:
            response = requests.post(
                self.ollama_url,
                json={
                    "model": self.model,
                    "messages": [
                        {"role": "system", "content": (
                            "Du bist ein Krypto Research Analyst. Maximal 300 Wörter. "
                            "Keine Wiederholungen. Nur Substanz, kein Filler. "
                            "Trenne Fakten von Meinung. Unsicherheit ehrlich flaggen. "
                            "Nie Handlungsempfehlungen, nur Informationen."
                        )},
                        {"role": "user", "content": prompt}
                    ],
                    "stream": False,
                    "max_tokens": max_tokens
                },
                timeout=120
            )
            data = response.json()
            return data["choices"][0]["message"]["content"]
        except Exception as e:
            return f"[Researcher] Fehler bei Kimi-Anfrage: {e}"

    def daily_research(self, scanner_data: list = None, polymarket_signals: dict = None) -> str:
        context = ""
        if scanner_data:
            for scan in scanner_data:
                context += (
                    f"- {scan['symbol']}: Confluence={scan['confluence']['level']}, "
                    f"24h={scan.get('change_24h', 0)}%, "
                    f"Funding={scan.get('funding_rate', 0)}\n"
                )

        poly_context = ""
        if polymarket_signals:
            if polymarket_signals.get("bullish_signals"):
                poly_context += "BULLISH Polymarket Signale:\n"
                for s in polymarket_signals["bullish_signals"][:3]:
                    poly_context += f"  - {s}\n"
            if polymarket_signals.get("risk_signals"):
                poly_context += "RISK Polymarket Signale:\n"
                for s in polymarket_signals["risk_signals"][:3]:
                    poly_context += f"  - {s}\n"
            if polymarket_signals.get("highlights"):
                poly_context += "Top Prediction Markets:\n"
                for h in polymarket_signals["highlights"]:
                    poly_context += f"  - {h}\n"

        prompt = f"""Erstelle den täglichen Krypto Research Brief. Datum: {datetime.now().strftime('%Y-%m-%d')}

AKTUELLE SCANNER-DATEN:
{context if context else 'Keine Scanner-Daten verfügbar.'}

POLYMARKET PREDICTION MARKETS:
{poly_context if poly_context else 'Keine Polymarket-Daten verfügbar.'}

Analysiere basierend auf deinem Wissen:
1. Aktuelle Markt-Stimmung und wichtigste Events
2. Bitcoin Dominance Trend und Risk-Appetite Einschätzung
3. Funding Rates Analyse der gescannten Pairs
4. Aktuelle Narrative Rotationen (AI, RWA, DePIN, Memes, L2s)
5. Fear & Greed Einschätzung
6. BTC Korrelation mit TradFi

FORMAT:
MARKT-STIMMUNG: (Bullish/Neutral/Bearish) + 1 Satz Begründung
TOP 3 EVENTS: kurz gelistet
NARRATIVE: was ist diese Woche heiß
RISK: was könnte den Markt crashen
CHANCE: was wird übersehen"""

        report = self._ask_kimi(prompt)
        self._log_research("daily", report)
        return report

    def weekly_deep_dive(self, scanner_data: list = None) -> str:
        context = ""
        if scanner_data:
            for scan in scanner_data:
                context += f"- {scan['symbol']}: {scan['confluence']['level']}\n"

        prompt = f"""Erstelle den wöchentlichen Deep Dive. Woche: {datetime.now().strftime('%Y-W%W')}

SCANNER-ÜBERSICHT:
{context if context else 'Keine Daten.'}

Analysiere:
1. Makro-Kalender kommende Woche (FOMC, CPI, Jobs falls relevant)
2. ETF Flow Einschätzung (BTC + ETH)
3. Sektor-Rotation mit Conviction Rankings
4. DeFi TVL Trends
5. Contrarian Signals

FORMAT:
MAKRO: wichtigste Events
ETF FLOWS: Trend
SEKTOR-ROTATION: Top 3 Sektoren mit Conviction (HIGH/MED/LOW)
CONTRARIAN: was der Markt übersieht
RISIKEN: Top 3 Downside-Risiken"""

        report = self._ask_kimi(prompt, max_tokens=1200)
        self._log_research("weekly", report)
        return report

    def analyze_narrative_shift(self, event: str) -> str:
        prompt = f"""SOFORT-ANALYSE: {event}

Bewerte in max 100 Wörtern:
1. Marktrelevanz (HIGH/MED/LOW)
2. Betroffene Coins/Sektoren
3. Erwartete Auswirkung (kurzfristig/langfristig)
4. Unsicherheitslevel"""

        return self._ask_kimi(prompt, max_tokens=300)

    def _log_research(self, report_type: str, content: str):
        date_str = datetime.now().strftime("%Y-%m-%d")
        log_file = os.path.join(config.LOG_DIR, f"research_{date_str}.json")

        logs = []
        if os.path.exists(log_file):
            try:
                with open(log_file, "r") as f:
                    logs = json.load(f)
            except:
                logs = []

        logs.append({
            "type": report_type,
            "time": datetime.now(timezone.utc).isoformat(),
            "content": content
        })

        with open(log_file, "w") as f:
            json.dump(logs, f, indent=2, ensure_ascii=False)
