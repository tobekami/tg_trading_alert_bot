import sys
import os

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))
from app.state import StateManager

def test_state_manager():
    print("\n--- 🧠 Testing State Manager ---")
    test_file = "data/test_state.pkl"
    if os.path.exists(test_file): os.remove(test_file)

    try:
        state = StateManager(filepath=test_file)
        symbol = "TEST/USDT"

        # 1. Rolling Cache
        for i in range(155):
            cache = state.update_candle_cache(symbol, {"close": i})
            if i == 149: assert len(cache) == 150
            elif i == 150: assert len(cache) == 101

        # 2. Alert Cooldowns
        assert state.can_alert(symbol, "EQ_0.5", 1000, 900) is True
        assert state.can_alert(symbol, "EQ_0.5", 1010, 900) is False
        assert state.can_alert(symbol, "EQ_0.5", 1905, 900) is True

        # 3. Persistence
        state.save_state()
        assert os.path.exists(test_file)
        new_state = StateManager(filepath=test_file)
        assert len(new_state.state["candle_caches"][symbol]) == 105

    finally:
        if os.path.exists(test_file): os.remove(test_file)
        if os.path.exists(f"{test_file}.bak"): os.remove(f"{test_file}.bak")