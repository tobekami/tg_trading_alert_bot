"""
Purpose:
    Loads and validates environment variables from the .env file.
    This acts as the central configuration hub for the bot.

Returns:
    Config (class): A class containing all validated configuration variables.

Example:
    from app.config import Config
    print(Config.TWELVE_DATA_KEY)
"""
import os
from dotenv import load_dotenv

# We use os.path to dynamically locate the .env file in the parent directory.
# This ensures the bot can be run from any working directory without path errors.
try:
    dotenv_path = os.path.join(os.path.dirname(__file__), '..', '.env')
    load_dotenv(dotenv_path=dotenv_path)
except Exception as e:
    # Catch generic exceptions in case of strict permission errors reading the file
    print(f"Warning: Could not load .env file directly. Relying on system environment variables. Error: {e}")


class Config:
    # Binance Keys (Optional)
    # Why: Binance allows public market data fetching (like candles) without an API key.
    # Rate limits for public endpoints are tracked by IP address, allowing a generous 6,000 request weight per minute, which easily covers our needs.
    BINANCE_API_KEY = os.getenv("BINANCE_API_KEY", None)
    BINANCE_SECRET = os.getenv("BINANCE_SECRET", None)

    # OANDA (Required for Forex/Indices)
    OANDA_API_KEY = os.getenv("OANDA_API_KEY")
    # 'practice' routes to api-fxpractice.oanda.com, 'live' routes to api-fxtrade.oanda.com
    OANDA_ENV = os.getenv("OANDA_ENV", "practice")

    # Telegram (Required for Alerts)
    TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
    TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

    @classmethod
    def check_config(cls) -> None:
        """
        Purpose:
            Validates that all mandatory environment variables are set.
            Raises an error immediately on startup if keys are missing, preventing runtime crashes.

        Args:
            None

        Returns:
            None

        Raises:
            EnvironmentError: If TWELVE_DATA_KEY, TELEGRAM_BOT_TOKEN, or TELEGRAM_CHAT_ID are missing.

        Example:
            Config.check_config() # Will raise EnvironmentError if .env is incomplete
        """
        missing = []

        # We only check for Twelve Data and Telegram because Binance keys are optional for our public data use case.
        if not cls.OANDA_API_KEY:
            missing.append("OANDA_API_KEY")
        if not cls.TELEGRAM_BOT_TOKEN:
            missing.append("TELEGRAM_BOT_TOKEN")
        if not cls.TELEGRAM_CHAT_ID:
            missing.append("TELEGRAM_CHAT_ID")

        if missing:
            raise EnvironmentError(f"CRITICAL: Missing required .env variables: {', '.join(missing)}")