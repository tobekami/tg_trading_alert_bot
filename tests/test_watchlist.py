import sys
import os

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))
from app.state import StateManager

def test_dynamic_watchlist():
    print("\n--- 🧠 Testing Dynamic Watchlist (State Manager) ---")
    test_file = "data/test_watchlist_state.pkl"
    if os.path.exists(test_file): os.remove(test_file)

    try:
        state = StateManager(filepath=test_file)

        # 1. Addition (Using ADA to avoid colliding with default BTC/SOL)
        assert state.add_symbol("ADA/USDT", "crypto") is True
        assert "ADA/USDT" in state.get_watchlist()
        assert state.add_symbol("ADA/USDT", "crypto") is False # Duplicate

        # 2. Levels
        assert state.update_levels("ADA/USDT", [0.382, 0.618]) is True
        assert state.get_watchlist()["ADA/USDT"]["levels"] == [0.382, 0.618]

        # 3. Toggles
        assert state.toggle_alert("ADA/USDT", "bos") == "OFF"
        assert state.get_watchlist()["ADA/USDT"]["alerts"]["bos"] is False

        # 4. Removal
        assert state.remove_symbol("ADA/USDT") is True
        assert "ADA/USDT" not in state.get_watchlist()

    finally:
        if os.path.exists(test_file): os.remove(test_file)
        if os.path.exists(f"{test_file}.bak"): os.remove(f"{test_file}.bak")