"""
Purpose:
    The Main Event Loop. Orchestrates concurrent execution of the
    Telegram CLI (listening) and the Market Scanner (processing).
    Fully upgraded for Phase 4.5 to support Multi-Timeframe (MTF) Composite Keys.
"""
import asyncio
import logging
import pandas as pd
import time
from typing import List, Tuple

from app.data_manager import DataManager, SyncScheduler
from app.state import StateManager
from app.structure import MarketStructureOrchestrator
from app.scanner import PatternScanner
from app.telegram_handler import TelegramCLI

# Configure production-grade logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(message)s')
logger = logging.getLogger(__name__)

# --- HIDE TELEGRAM NETWORK SPAM ---
logging.getLogger("httpx").setLevel(logging.WARNING)


class OmniMarketSentinel:
    """
    Purpose:
        The central nervous system of the bot. Manages state, triggers data fetching,
        routes data through the structural brain, and dispatches alerts.
    """

    def __init__(self):
        """
        Initializes the Core Managers and the Telegram User Interface.
        """
        self.state_manager = StateManager()
        self.data_manager = DataManager()
        self.telegram = TelegramCLI(self.state_manager)

        # Mapping for dynamic live-candle dropping based on timeframe duration
        self.tf_seconds_map = {
            '3m': 180,
            '15m': 900,
            '1h': 3600,
            '4h': 14400,
            '1d': 86400
        }

    async def preload_history(self) -> None:
        """
        Purpose:
            Fetches 400 historical candles on startup to build accurate L1/L2 structures.
            Now utilizes the Composite Key architecture (Symbol:Timeframe).
        """
        watchlist = self.state_manager.get_watchlist()
        fetch_requests: List[Tuple[str, str, str, str]] = []

        # 1. Build the Composite Fetch Requests
        for composite_key, cfg in watchlist.items():
            # Fallback for old save files that might not have the colon formatting yet
            symbol = composite_key.split(':')[0] if ':' in composite_key else composite_key
            m_type = cfg.get("type", "crypto")
            tf = cfg.get("timeframe", "3m")

            fetch_requests.append((composite_key, symbol, m_type, tf))

        if not fetch_requests:
            return

        logger.info("⏳ Pre-loading 400 historical candles to build Market Structure...")

        # 2. Fetch the data using the Phase 4.5 signature
        market_data = await self.data_manager.fetch_all_markets(fetch_requests, limit=400)

        # 3. Process the historical data per Composite Key
        for composite_key, df in market_data.items():
            if df.empty:
                continue

            # --- THE MEMORY WIPE FIX ---
            # Prevents time-overlap corruption on restart by cleanly wiping the old orchestrator
            self.state_manager.state["orchestrators"][composite_key] = MarketStructureOrchestrator()

            orchestrator = self.state_manager.get_orchestrator(composite_key)
            cache = []

            # Push all historical candles through the Brain Vault sequentially
            for index, row in df.iterrows():
                current_candle = {
                    "timestamp": index,
                    "open": float(row['open']),
                    "high": float(row['high']),
                    "low": float(row['low']),
                    "close": float(row['close'])
                }
                cache.append(current_candle)

                # Calculate historical ATR dynamically
                if len(cache) >= 14:
                    recent_highs = [c['high'] for c in cache[-14:]]
                    recent_lows = [c['low'] for c in cache[-14:]]
                    current_atr = (sum(h - l for h, l in zip(recent_highs, recent_lows)) / 14.0) * 1.0
                else:
                    current_atr = (current_candle['high'] - current_candle['low']) * 1.0

                orchestrator.process_candle(
                    current_candle['high'],
                    current_candle['low'],
                    current_candle['timestamp'],
                    current_atr
                )

            # Cap the memory vault to the most recent 150 candles
            self.state_manager.state["candle_caches"][composite_key] = cache[-150:]
            logger.info(f"   -> ✅ {composite_key} Structure mapped.")

        self.state_manager.save_state()
        logger.info("🧠 Brain Vault initialized with historical MTF data.")

    async def trading_loop(self) -> None:
        """
        Purpose:
            The 24/7 background loop that scans markets.
            Wakes up every 3 minutes, but dynamically processes higher timeframes
            only when their respective candles actually close.
        """
        logger.info("📈 Trading Loop active. Syncing to clock...")

        while True:
            try:
                # Sync to the global 3-minute heartbeat
                await SyncScheduler.sleep_until_next_candle(interval_minutes=3)
                logger.info("⚡ Processing markets...")

                # --- START THE STOPWATCH ---
                start_time = time.perf_counter()

                # 1. Pull the dynamic watchlist and build requests
                watchlist = self.state_manager.get_watchlist()
                fetch_requests: List[Tuple[str, str, str, str]] = []

                for composite_key, cfg in watchlist.items():
                    symbol = composite_key.split(':')[0] if ':' in composite_key else composite_key
                    m_type = cfg.get("type", "crypto")
                    tf = cfg.get("timeframe", "3m")
                    fetch_requests.append((composite_key, symbol, m_type, tf))

                # Skip iteration if watchlist was cleared via Telegram
                if not fetch_requests:
                    logger.info("📭 Watchlist empty. Waiting for Telegram commands...")
                    continue

                # 2. Fetch Data (limit=150 for catch-up recovery)
                market_data = await self.data_manager.fetch_all_markets(fetch_requests, limit=150)
                all_alerts = []

                # 3. Process Symbols
                for composite_key, df in market_data.items():
                    if df.empty:
                        continue

                    config = watchlist[composite_key]
                    tf = config.get("timeframe", "3m")
                    scanner = PatternScanner(config)
                    orchestrator = self.state_manager.get_orchestrator(composite_key)

                    # --- THE BATCH CATCH-UP LOGIC ---
                    candle_cache = self.state_manager.state.get("candle_caches", {}).get(composite_key, [])
                    if candle_cache:
                        last_cache_time = pd.to_datetime(candle_cache[-1]['timestamp']).tz_localize(None)
                    else:
                        last_cache_time = pd.to_datetime(0).tz_localize(None)

                    current_time_naive = pd.Timestamp.now('UTC').tz_localize(None)
                    df.index = pd.to_datetime(df.index).tz_localize(None)

                    time_diff_seconds = (current_time_naive - df.index[-1]).total_seconds()

                    # --- DYNAMIC TIMEFRAME GATEKEEPER ---
                    # Drops the currently forming candle based on its specific timeframe length
                    required_duration = self.tf_seconds_map.get(tf, 180)
                    if time_diff_seconds < required_duration:
                        df_closed = df.iloc[:-1]
                    else:
                        df_closed = df

                    # Isolate missing candles that closed AFTER our last saved cache time
                    new_candles = df_closed[df_closed.index > last_cache_time]

                    if new_candles.empty:
                        continue

                    # Accumulators for this specific cycle
                    batch_l1_pivots = []
                    batch_l2_pivots = []

                    # Process the missing gap sequentially
                    for idx, row in new_candles.iterrows():
                        current_candle = {
                            "timestamp": idx,
                            "open": float(row['open']),
                            "high": float(row['high']),
                            "low": float(row['low']),
                            "close": float(row['close'])
                        }

                        current_cache = self.state_manager.update_candle_cache(composite_key, current_candle)

                        # Calculate 14-period ATR
                        if len(current_cache) >= 14:
                            recent_highs = [c['high'] for c in current_cache[-14:]]
                            recent_lows = [c['low'] for c in current_cache[-14:]]
                            current_atr = (sum(h - l for h, l in zip(recent_highs, recent_lows)) / 14.0) * 1.0
                        else:
                            current_atr = (current_candle['high'] - current_candle['low']) * 1.0

                        # Feed into Brain Vault
                        l0, l1_list, l2_list = orchestrator.process_candle(
                            current_candle['high'],
                            current_candle['low'],
                            current_candle['timestamp'],
                            current_atr
                        )

                        if l1_list: batch_l1_pivots.extend(l1_list)
                        if l2_list: batch_l2_pivots.extend(l2_list)

                        # Scan for alerts ONLY on the absolute newest (last) candle in the catch-up batch
                        if idx == new_candles.index[-1]:
                            symbol_alerts = scanner.scan(
                                composite_key,  # Pass the composite key so alerts show the timeframe
                                current_candle,
                                current_cache,
                                orchestrator,
                                batch_l1_pivots,
                                batch_l2_pivots,
                                self.state_manager
                            )

                            logger.info(f"   -> {composite_key} analyzed. Current Close: {current_candle['close']}")

                            if symbol_alerts:
                                all_alerts.extend([f"<b>{composite_key}</b> | {a}" for a in symbol_alerts])

                # 4. Dispatch Alerts & Save State
                for alert in all_alerts:
                    await self.telegram.send_alert(alert)

                self.state_manager.save_state()

                # --- STOP THE STOPWATCH & SAVE THE TIME ---
                end_time = time.perf_counter()
                loop_duration = round(end_time - start_time, 2)
                self.state_manager.state["last_loop_time"] = loop_duration
                logger.info(f"⏱️ Cycle completed in {loop_duration}s")

            except Exception as e:
                # Catch-all to prevent a single bad iteration from killing the 24/7 background loop
                logger.error(f"❌ Critical error in trading loop: {e}", exc_info=True)
                await asyncio.sleep(10) # Brief pause before attempting the next cycle

    async def run_concurrently(self) -> None:
        """
        Purpose:
            Initializes the Telegram bot and runs the trading loop alongside it asynchronously.
        """
        logger.info("🚀 Omni-Market Sentinel Starting...")

        # Start Telegram Application internals
        await self.telegram.app.initialize()
        await self.telegram.app.start()
        await self.telegram.app.updater.start_polling()

        logger.info("📱 Telegram CLI Online. Type /help in your chat.")

        # Load historical L1/L2 structures
        await self.preload_history()

        # Create the background task for the trading scanner
        trading_task = asyncio.create_task(self.trading_loop())

        try:
            # Keep the main thread alive indefinitely
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            logger.info("🛑 Shutting down...")
        finally:
            trading_task.cancel()
            await self.telegram.app.updater.stop()
            await self.telegram.app.stop()
            await self.telegram.app.shutdown()
            await self.data_manager.close_connections()

if __name__ == "__main__":
    bot = OmniMarketSentinel()
    try:
        asyncio.run(bot.run_concurrently())
    except KeyboardInterrupt:
        print("\n[ℹ️] Manual interrupt received. Exiting.")