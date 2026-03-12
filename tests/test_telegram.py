import sys
import os
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))
from app.state import StateManager
from app.telegram_handler import TelegramCLI


@pytest.mark.asyncio
async def test_telegram_logic():
    print("\n--- 📱 Testing Telegram CLI UI Routers ---")

    # Cleanup before test
    if os.path.exists("data/test_tg_state.pkl"): os.remove("data/test_tg_state.pkl")
    if os.path.exists("data/test_tg_state.pkl.bak"): os.remove("data/test_tg_state.pkl.bak")

    try:
        state = StateManager(filepath="data/test_tg_state.pkl")

        with patch('app.telegram_handler.Application.builder') as mock_builder:
            cli = TelegramCLI(state)

        update = MagicMock()
        update.message = AsyncMock()
        context = MagicMock()

        # 1. Test /add Command (Should return the Interactive Keyboard)
        await cli.cmd_add(update, context)
        update.message.reply_text.assert_called()
        reply_text = update.message.reply_text.call_args[0][0]
        kwargs = update.message.reply_text.call_args[1]

        assert "What type of market are you adding?" in reply_text, "Failed to send the interactive prompt."
        assert 'reply_markup' in kwargs, "Failed to attach the Inline Keyboard."

        # 2. Test /status Command (Empty Watchlist via Mocking)
        # We mock the get_watchlist function to bypass the auto-repopulate safety net
        with patch.object(state, 'get_watchlist', return_value={}):
            await cli.cmd_status(update, context)
            reply_text = update.message.reply_text.call_args[0][0]
            assert "empty" in reply_text.lower(), "Did not handle empty watchlist correctly."

        # 3. Test /status Command (Populated Watchlist)
        state.add_symbol("BTC/USDT:15m", "crypto")
        await cli.cmd_status(update, context)
        reply_text = update.message.reply_text.call_args[0][0]
        assert "Active Watchlist" in reply_text, "Did not return active watchlist status."
        assert "BTC/USDT:15m" in reply_text, "Did not display the added composite symbol."

        # 4. Test /levels Command (Should return the Watchlist Keyboard)
        await cli.cmd_levels(update, context)
        reply_text = update.message.reply_text.call_args[0][0]
        assert "Select the pair to update levels for:" in reply_text, "Failed to return the levels interactive prompt."

    finally:
        # Cleanup after test finishes or fails
        if os.path.exists("data/test_tg_state.pkl"): os.remove("data/test_tg_state.pkl")
        if os.path.exists("data/test_tg_state.pkl.bak"): os.remove("data/test_tg_state.pkl.bak")