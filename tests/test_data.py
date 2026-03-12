import sys
import os
import pytest

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))
from app.config import Config
from app.data_manager import DataManager, SyncScheduler

@pytest.mark.asyncio
async def test_data_manager():
    print("\n--- ⚡ Testing Phase 3: Data Manager & Async Scheduler ---")

    Config.check_config()
    manager = DataManager()

    # We pass tuples now based on our Phase 4.5 MTF updates
    fetch_requests = [
        ("BTC/USDT:3m", "BTC/USDT", "crypto", "3m"),
        ("US30_USD:3m", "US30_USD", "forex", "3m")
    ]

    try:
        market_data = await manager.fetch_all_markets(fetch_requests, limit=5)

        for composite_key, df in market_data.items():
            assert not df.empty, f"DataFrame for {composite_key} is empty."
            assert 'close' in df.columns, f"Missing OHLCV columns in {composite_key}"

        # We skip testing the SyncScheduler clock in CI/CD so the pipeline doesn't hang for 3 minutes

    finally:
        await manager.close_connections()