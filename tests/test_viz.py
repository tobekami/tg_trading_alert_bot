import sys
import os
import ccxt
import pandas as pd
import numpy as np

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))
from app.structure import MarketStructureOrchestrator
from app.visualizer import Visualizer


def test_visualization():
    print("\n--- 👁️ Testing Phase 2: Dual Visualization Engine ---")
    html_path = 'logs/plots/debug_chart.html'
    png_path = 'logs/plots/mobile_chart.png'

    # Cleanup before test
    if os.path.exists(html_path): os.remove(html_path)
    if os.path.exists(png_path): os.remove(png_path)

    try:
        exchange = ccxt.mexc({'enableRateLimit': True})
        candles = exchange.fetch_ohlcv('BTC/USDT', timeframe='15m', limit=150)

        df = pd.DataFrame(candles, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
        df['timestamp'] = pd.to_datetime(df['timestamp'], unit='ms')
        df.set_index('timestamp', inplace=True)
        df['atr'] = 100.0  # Mock ATR for speed

        orchestrator = MarketStructureOrchestrator()
        l0_col, l1_col, l2_col = [], [], []

        for index, row in df.iterrows():
            l0, l1, l2 = orchestrator.process_candle(row['high'], row['low'], index, row['atr'] * 0.5)
            if l0: l0_col.append(l0)
            if l1: l1_col.extend(l1)
            if l2: l2_col.extend(l2)

        os.makedirs('logs/plots', exist_ok=True)
        viz = Visualizer()

        viz.generate_html_chart(df, l0_col, l1_col, l2_col, save_path=html_path)
        viz.generate_static_chart(df, l1_col, l2_col, save_path=png_path)

        assert os.path.exists(html_path), "HTML chart failed to generate."
        assert os.path.exists(png_path), "Static PNG failed to generate."

    finally:
        if os.path.exists(html_path): os.remove(html_path)
        if os.path.exists(png_path): os.remove(png_path)