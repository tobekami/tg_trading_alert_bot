import sys
import os

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))
from app.structure import MarketStructureOrchestrator

def test_neurotrader_logic():
    print("\n--- 🧠 Testing Phase 1: Core Logic ---")
    orchestrator = MarketStructureOrchestrator()
    atr_threshold = 1.0

    synthetic_candles = [
        [1, 10, 9], [2, 12, 11], [3, 10, 9], [4, 8, 7], [5, 10, 9],
        [6, 14, 13], [7, 12, 11], [8, 9, 8], [9, 11, 10],
        [10, 11, 10], [11, 9, 8], [12, 6, 5], [13, 9, 8],
    ]

    l0_count = 0
    l1_count = 0

    for candle in synthetic_candles:
        timestamp, high, low = candle
        l0, l1, l2 = orchestrator.process_candle(high=high, low=low, timestamp=timestamp, atr=atr_threshold)
        if l0: l0_count += 1
        if l1: l1_count += len(l1)

    assert l0_count == 6, f"Expected 6 L0 Pivots, but got {l0_count}"
    assert l1_count == 1, f"Expected 1 L1 Pivot, but got {l1_count}"