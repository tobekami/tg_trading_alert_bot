import sys
import os

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))
from app.scanner import PatternScanner

def test_heikin_ashi_and_killzone():
    print("\n--- 🧠 Testing Phase 4: Heikin Ashi & ATR Killzone ---")
    config = {"levels": [0.5, 0.75], "alerts": {"reversal": True}}
    scanner = PatternScanner(config)

    # 1. ATR Killzone
    candle_in = {"low": 155.0, "high": 170.0}
    candle_out = {"low": 165.0, "high": 175.0}
    assert scanner._is_in_killzone(candle_in, 150.0, 10.0) is True, "Failed to detect price inside killzone."
    assert scanner._is_in_killzone(candle_out, 150.0, 10.0) is False, "False positive outside killzone."

    # 2. Heikin Ashi Math
    ohlc_cache = [
        {"open": 100, "high": 110, "low": 90, "close": 105},
        {"open": 105, "high": 120, "low": 100, "close": 115}
    ]
    ha_cache = scanner._calculate_ha(ohlc_cache)
    assert ha_cache[0]["close"] == 101.25, "HA Close math incorrect."

    # 3. HA Reversal Sequences
    ha_doji_green = [
        {"open": 100, "high": 105, "low": 95, "close": 101},
        {"open": 101, "high": 120, "low": 101, "close": 118}
    ]
    assert scanner._check_ha_reversal(ha_doji_green, is_bullish_pullback=True) is True, "Failed to detect HA sequence."