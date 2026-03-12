import sys
import os

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))
from app.scanner import PatternScanner
from app.state import StateManager
from app.structure import MarketStructureOrchestrator, Pivot

def test_pattern_scanner():
    print("\n--- 👁️ Testing Pattern Scanner ---")

    test_file = "data/test_scanner_state.pkl"
    if os.path.exists(test_file): os.remove(test_file)

    try:
        config = {"levels": [0.5], "alerts": {"bos": True, "reversal": True, "pivot": True}}
        scanner = PatternScanner(config)
        state = StateManager(filepath=test_file)
        orchestrator = MarketStructureOrchestrator()
        symbol = "BTC/USDT"

        orchestrator.l1_logic.confirmed_pivots = [
            Pivot(price=10000, timestamp=1, type=-1, level=1),
            Pivot(price=20000, timestamp=2, type=1, level=1)
        ]

        # 1. BOS One-Shot Logic
        candle_1 = {"timestamp": 3, "open": 19000, "high": 21000, "low": 19000, "close": 20500}
        alerts_1 = scanner.scan(symbol, candle_1, [], orchestrator, [], [], state)

        candle_2 = {"timestamp": 4, "open": 20500, "high": 21500, "low": 20000, "close": 21000}
        alerts_2 = scanner.scan(symbol, candle_2, [], orchestrator, [], [], state)

        assert any("BOS" in a for a in alerts_1), "Failed to detect initial BOS."
        assert not any("BOS" in a for a in alerts_2), "Failed to suppress redundant BOS alert."

    finally:
        if os.path.exists(test_file): os.remove(test_file)
        if os.path.exists(f"{test_file}.bak"): os.remove(f"{test_file}.bak")