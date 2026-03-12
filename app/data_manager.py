"""
Purpose:
    Manages all data retrieval, processing, and scheduling for the bot.
    Uses asynchronous programming (asyncio.to_thread) to fetch from multiple APIs concurrently.
    Supports dynamic timeframes, utilizing a hybrid approach (resampling 1m for 3m crypto charts,
    and using native exchange endpoints for 15m+).
"""
import asyncio
from datetime import datetime, timedelta
import pandas as pd
import requests
import threading
import ccxt
from typing import Dict, Optional, List, Tuple

from app.config import Config


class DataManager:
    """
    Purpose:
        Orchestrates data fetching for both Crypto (MEXC) and Forex (OANDA).
        Dynamically maps user-requested timeframes to exchange-specific granularities.
    """

    def __init__(self):
        """
        Initializes the DataManager, configuring API clients and timeframe mappings.
        """
        # Initialize MEXC with rate limiting enabled to prevent IP bans during concurrent fetching
        self.mexc = ccxt.mexc({'enableRateLimit': True})
        self.mexc_lock = threading.Lock()

        # Retrieve OANDA credentials from the central configuration
        self.oanda_key = Config.OANDA_API_KEY

        # Determine the appropriate endpoint based on the selected environment (practice vs. live)
        domain = "api-fxpractice.oanda.com" if Config.OANDA_ENV == "practice" else "api-fxtrade.oanda.com"
        self.oanda_url = f"https://{domain}/v3/instruments"

        # Pre-configure headers required for OANDA API authentication
        self.oanda_headers = {
            "Authorization": f"Bearer {self.oanda_key}",
            "Accept-Datetime-Format": "UNIX"  # Enforce UNIX timestamps to streamline Pandas conversion
        }

        # --- TIMEFRAME TRANSLATORS ---
        # Maps standard bot timeframes to OANDA's specific granularity string formats
        self.oanda_tf_map = {
            '3m': 'M3',
            '15m': 'M15',
            '1h': 'H1',
            '4h': 'H4',
            '1d': 'D'
        }

    def _fetch_mexc_sync(self, symbol: str, timeframe: str, limit: int) -> Optional[pd.DataFrame]:
        """
        Purpose:
            Fetches timeframe candles from MEXC synchronously.
            Uses a hybrid approach: Resamples 1m data for bulletproof 3m charts,
            but uses native exchange endpoints for higher timeframes to save memory.

        Args:
            symbol (str): The trading pair (e.g., 'BTC/USDT').
            timeframe (str): The requested timeframe (e.g., '3m', '15m').
            limit (int): The number of final candles to retrieve.

        Returns:
            Optional[pd.DataFrame]: A DataFrame containing the OHLCV data, or None if failed.

        Example:
            df = manager._fetch_mexc_sync('BTC/USDT', '3m', 150)
        """
        try:
            # --- HYBRID LOGIC: Manual Resampling for 3-minute charts ---
            if timeframe == '3m':
                # Fetch 3x the limit in 1-minute candles to ensure we have enough data to build the 3m blocks
                # Lock the socket so threads don't collide
                with self.mexc_lock:
                    candles = self.mexc.fetch_ohlcv(symbol, timeframe='1m', limit=limit * 3)

                if not candles:
                    print(f"⚠️ MEXC returned no data for {symbol} on 1m (for 3m resample)")
                    return None

                # Load raw 1-minute data into a Pandas DataFrame
                df = pd.DataFrame(candles, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
                df['timestamp'] = pd.to_datetime(df['timestamp'], unit='ms')
                df.set_index('timestamp', inplace=True)

                # Guard against empty DataFrames before attempting to resample to avoid Pandas errors
                if df.empty:
                    return None

                # Mathematically construct the 3-minute blocks using aggregation rules
                resampled_df = df.resample('3min').agg({
                    'open': 'first',  # The open of the first 1m candle
                    'high': 'max',    # The highest high of the three 1m candles
                    'low': 'min',     # The lowest low of the three 1m candles
                    'close': 'last',  # The close of the final 1m candle
                    'volume': 'sum'   # The total volume across the three 1m candles
                })

                # Drop any incomplete rows (e.g., if we fetch mid-minute)
                resampled_df.dropna(inplace=True)
                return resampled_df

            # --- HYBRID LOGIC: Native Fetching for 15m, 1h, 4h, 1d ---
            else:
                # Rely on the exchange's native aggregation for higher timeframes to minimize payload size
                with self.mexc_lock:
                    candles = self.mexc.fetch_ohlcv(symbol, timeframe=timeframe, limit=limit)

                if not candles:
                    print(f"⚠️ MEXC returned no data for {symbol} on {timeframe}")
                    return None

                # Parse directly into a DataFrame
                df = pd.DataFrame(candles, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
                df['timestamp'] = pd.to_datetime(df['timestamp'], unit='ms')
                df.set_index('timestamp', inplace=True)
                return df

        # Catch specific CCXT errors to differentiate between network failures and exchange rejections
        except ccxt.NetworkError as e:
            print(f"❌ Network Error fetching Crypto ({symbol} @ {timeframe}): {e}")
            return None
        except ccxt.ExchangeError as e:
            print(f"❌ Exchange Error fetching Crypto ({symbol} @ {timeframe}): {e}")
            return None
        except Exception as e:
            print(f"❌ Unexpected Error fetching Crypto ({symbol} @ {timeframe}): {e}")
            return None

    async def fetch_mexc_crypto(self, symbol: str, timeframe: str = '3m', limit: int = 10) -> Optional[pd.DataFrame]:
        """
        Purpose:
            Wraps the synchronous MEXC request in an asyncio thread to prevent blocking the event loop.

        Args:
            symbol (str): The trading pair (e.g., 'BTC/USDT').
            timeframe (str, optional): The requested timeframe. Defaults to '3m'.
            limit (int, optional): The number of candles to retrieve. Defaults to 10.

        Returns:
            Optional[pd.DataFrame]: The resulting OHLCV DataFrame.
        """
        return await asyncio.to_thread(self._fetch_mexc_sync, symbol, timeframe, limit)

    def _fetch_oanda_sync(self, symbol: str, timeframe: str, limit: int) -> Optional[pd.DataFrame]:
        """
        Purpose:
            Translates the timeframe and fetches native candles from OANDA synchronously.
            OANDA natively supports M3, so no manual resampling is required here.

        Args:
            symbol (str): The forex instrument (e.g., 'EUR_USD').
            timeframe (str): The standard bot timeframe (e.g., '3m', '15m').
            limit (int): The number of candles to retrieve.

        Returns:
            Optional[pd.DataFrame]: A DataFrame containing the OHLCV data, or None if failed.

        Example:
            df = manager._fetch_oanda_sync('EUR_USD', '15m', 150)
        """
        try:
            # Map the standard timeframe to OANDA's required format, defaulting to 'M3'
            oanda_granularity = self.oanda_tf_map.get(timeframe, 'M3')

            # Construct the REST API endpoint URL
            url = f"{self.oanda_url}/{symbol}/candles?granularity={oanda_granularity}&count={limit}"

            # Execute the GET request with a 10-second timeout to prevent hanging
            response = requests.get(url, headers=self.oanda_headers, timeout=10)
            response.raise_for_status()

            data = response.json()

            # Validate that the expected 'candles' payload exists
            if 'candles' not in data or not data['candles']:
                print(f"⚠️ OANDA returned no candle data for {symbol} on {timeframe}")
                return None

            parsed_data = []
            # Iterate through the JSON response and extract the midpoint ('mid') prices
            for candle in data['candles']:
                parsed_data.append({
                    'timestamp': float(candle['time']) * 1000,  # Convert seconds to milliseconds
                    'open': float(candle['mid']['o']),
                    'high': float(candle['mid']['h']),
                    'low': float(candle['mid']['l']),
                    'close': float(candle['mid']['c']),
                    'volume': float(candle['volume'])
                })

            # Convert the list of dictionaries into a Pandas DataFrame
            df = pd.DataFrame(parsed_data)
            df['timestamp'] = pd.to_datetime(df['timestamp'], unit='ms')
            df.set_index('timestamp', inplace=True)
            return df

        # Catch specific HTTP errors (e.g., 401 Unauthorized, 404 Not Found)
        except requests.exceptions.RequestException as e:
            print(f"❌ HTTP Error fetching Forex ({symbol} @ {timeframe}): {e}")
            return None
        except Exception as e:
            print(f"❌ Unexpected Error fetching Forex ({symbol} @ {timeframe}): {e}")
            return None

    async def fetch_oanda_forex(self, symbol: str, timeframe: str = '3m', limit: int = 5) -> Optional[pd.DataFrame]:
        """
        Purpose:
            Wraps the synchronous OANDA request in an asyncio thread to prevent blocking the event loop.

        Args:
            symbol (str): The forex instrument (e.g., 'US30_USD').
            timeframe (str, optional): The standard bot timeframe. Defaults to '3m'.
            limit (int, optional): The number of candles to retrieve. Defaults to 5.

        Returns:
            Optional[pd.DataFrame]: The resulting OHLCV DataFrame.
        """
        return await asyncio.to_thread(self._fetch_oanda_sync, symbol, timeframe, limit)

    async def fetch_all_markets(self, fetch_requests: List[Tuple[str, str, str, str]], limit: int = 5) -> Dict[str, pd.DataFrame]:
        """
        Purpose:
            Fires off all data requests simultaneously based on specific timeframe tasks.

        Args:
            fetch_requests (List[Tuple[str, str, str, str]]): A list of tuples containing
                (composite_key, symbol, market_type, timeframe).
            limit (int, optional): The number of candles to retrieve per request. Defaults to 5.

        Returns:
            Dict[str, pd.DataFrame]: A dictionary mapping composite keys to their respective DataFrames.

        Example:
            requests = [("BTC/USDT:15m", "BTC/USDT", "crypto", "15m")]
            results = await manager.fetch_all_markets(requests, limit=150)
        """
        results = {}
        tasks = []

        # Determine if any crypto requests exist to handle lazy-loading of markets
        has_crypto = any(req[2] == 'crypto' for req in fetch_requests)

        # Lazy-load MEXC markets in a background thread to prevent collisions during concurrent fetching
        if has_crypto and not self.mexc.markets:
            print("[*] Lazy-loading MEXC market data to prevent thread collisions...")
            await asyncio.to_thread(self.mexc.load_markets)

        # 1. Queue all asynchronous fetch tasks based on market type
        for composite_key, symbol, market_type, timeframe in fetch_requests:
            if market_type == 'crypto':
                tasks.append(self.fetch_mexc_crypto(symbol, timeframe, limit))
            elif market_type == 'forex':
                tasks.append(self.fetch_oanda_forex(symbol, timeframe, limit))

        # 2. Execute all tasks concurrently using asyncio.gather
        print(f"[*] Fetching {len(tasks)} independent timeframe arrays concurrently...")
        completed_data = await asyncio.gather(*tasks)

        # 3. Map the results strictly back to their COMPOSITE KEYS (e.g., "BTC/USDT:15m")
        # We iterate through the original requests and the ordered results simultaneously
        for request_data, dataframe in zip(fetch_requests, completed_data):
            composite_key = request_data[0]
            if dataframe is not None:
                results[composite_key] = dataframe

        return results

    async def close_connections(self):
        """
        Purpose:
            Maintains compatibility with async context managers or shutdown routines.
            No active sockets need closing since we use synchronous HTTP under the hood.
        """
        pass


class SyncScheduler:
    """
    Purpose:
        Calculates the exact number of seconds until the next N-minute boundary.
        Remains untouched, natively supporting the global 3-minute heartbeat or other intervals.
    """

    @staticmethod
    async def sleep_until_next_candle(interval_minutes: int = 3):
        """
        Purpose:
            Pauses the execution until the clock reaches the next interval boundary.

        Args:
            interval_minutes (int, optional): The minute boundary to sync to. Defaults to 3.

        Example:
            await SyncScheduler.sleep_until_next_candle(interval_minutes=15)
        """
        now = datetime.utcnow()

        # Calculate the current minute within the hour
        current_minute = now.minute

        # Determine the next multiple of the interval_minutes
        next_minute_boundary = ((current_minute // interval_minutes) + 1) * interval_minutes

        # Construct the exact datetime object for the target wake-up time
        # We add a 2-second delay (second=2) to allow exchanges to finalize their candle data
        target_time = now.replace(minute=0, second=2, microsecond=0) + timedelta(minutes=next_minute_boundary)

        # Calculate the precise number of seconds remaining
        sleep_seconds = (target_time - now).total_seconds()

        print(f"[⏰] Clock Sync: Sleeping for {sleep_seconds:.1f} seconds. Waking up at {target_time.strftime('%H:%M:%S')} UTC...")

        # Yield control back to the event loop until the sleep completes
        await asyncio.sleep(sleep_seconds)