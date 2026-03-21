"""
Purpose:
    The Main Event Loop. Orchestrates concurrent execution of the
    Telegram CLI (listening) and the Market Scanner (processing).
    Fully upgraded for Phase 4.5 to support Multi-Timeframe (MTF) Composite Keys,
    and includes the Remote Debugger Gatekeeper for periodic HTML structural snapshots.
"""
import asyncio
import logging
import pandas as pd
import time
from datetime import datetime
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
            Fetches 1000 historical candles on startup for NEW pairs to build accurate structures.
            Bypasses existing pairs to allow StateManager and trading_loop to handle safe catch-up.
        """
        watchlist = self.state_manager.get_watchlist()
        fetch_requests: List[Tuple[str, str, str, str]] = []

        # 1. Build the Composite Fetch Requests
        for composite_key, cfg in watchlist.items():
            # If the symbol already has cached memory, skip it.
            if composite_key in self.state_manager.state.get("candle_caches", {}):
                logger.info(f"⏭️ {composite_key} memory intact. Skipping preload.")
                continue

            symbol = composite_key.split(':')[0] if ':' in composite_key else composite_key
            m_type = cfg.get("type", "crypto")
            tf = cfg.get("timeframe", "3m")

            fetch_requests.append((composite_key, symbol, m_type, tf))

        if not fetch_requests:
            logger.info("🧠 Brain Vaults already loaded from disk. Skipping preload.")
            return

        logger.info("⏳ Pre-loading 1000 historical candles to build Market Structure...")

        market_data = await self.data_manager.fetch_all_markets(fetch_requests, limit=1000)

        for composite_key, df in market_data.items():
            if df.empty:
                continue

            # Cleanly wipe the old orchestrator to prevent time-overlap corruption
            self.state_manager.state["orchestrators"][composite_key] = MarketStructureOrchestrator()
            orchestrator = self.state_manager.get_orchestrator(composite_key)
            cache = []

            for index, row in df.iterrows():
                current_candle = {
                    "timestamp": index,
                    "open": float(row['open']),
                    "high": float(row['high']),
                    "low": float(row['low']),
                    "close": float(row['close'])
                }
                cache.append(current_candle)

                if len(cache) >= 14:
                    recent_highs = [c['high'] for c in cache[-14:]]
                    recent_lows = [c['low'] for c in cache[-14:]]
                    current_atr = (sum(h - l for h, l in zip(recent_highs, recent_lows)) / 14.0) * 1.0
                else:
                    current_atr = (current_candle['high'] - current_candle['low']) * 1.0

                orchestrator.process_candle(
                    current_candle['high'], current_candle['low'], current_candle['timestamp'], current_atr
                )

            self.state_manager.state["candle_caches"][composite_key] = cache[-150:]
            logger.info(f"   -> ✅ {composite_key} Structure mapped.")

        self.state_manager.save_state()
        logger.info("🧠 Brain Vault initialized with historical MTF data.")

    def _build_snapshot_block(self, symbol: str, current_candle: dict, candle_cache: list,
                              orchestrator: MarketStructureOrchestrator, config: dict) -> str:
        """
        Purpose:
            Extracts structure data to build a beautifully formatted HTML block for Telegram.
            Taps into PatternScanner for math logic without triggering redundant alerts.
        """
        try:
            scanner = PatternScanner(config)

            # Calculate ATR for this specific snapshot moment
            if len(candle_cache) >= 14:
                recent_highs = [c['high'] for c in candle_cache[-14:]]
                recent_lows = [c['low'] for c in candle_cache[-14:]]
                current_atr = (sum(h - l for h, l in zip(recent_highs, recent_lows)) / 14.0) * 1.0
            else:
                current_atr = (current_candle['high'] - current_candle['low']) * 1.0

            last_l1_top = scanner._get_last_pivot(orchestrator.l1_logic.confirmed_pivots, 1)
            last_l1_bot = scanner._get_last_pivot(orchestrator.l1_logic.confirmed_pivots, -1)
            last_l2_top = scanner._get_last_pivot(orchestrator.l2_logic.confirmed_pivots, 1)
            last_l2_bot = scanner._get_last_pivot(orchestrator.l2_logic.confirmed_pivots, -1)

            # Initialize the UI block with Telegram's Blockquote tag
            block = f"📊 <b>{symbol}</b> | Close: <code>{current_candle['close']:.4f}</code>\n<blockquote>"

            def format_level(name, s_top, s_bot):
                """Internal helper to construct the L1/L2 string formatting."""
                if not s_top or not s_bot:
                    return f"<b>{name}</b> [Awaiting Anchors]\n\n"

                range_high, range_low, is_bull, is_slide = scanner._get_active_range(s_top, s_bot, orchestrator, current_candle)
                s_range = range_high - range_low
                leg_dir = "BULLISH (Bot->Top)" if is_bull else "BEARISH (Top->Bot)"
                leg_state = "DYNAMIC" if is_slide else "STATIC"

                eq_50 = range_high - (s_range * 0.5) if is_bull else range_low + (s_range * 0.5)
                eq_75 = range_high - (s_range * 0.75) if is_bull else range_low + (s_range * 0.75)

                return (f"<b>{name}</b> [{leg_state} {leg_dir}]\n"
                        f"{range_low:.4f} ➡️ {range_high:.4f} | 50%: {eq_50:.2f} | 75%: {eq_75:.2f}\n\n")

            block += format_level("L2", last_l2_top, last_l2_bot)
            block += format_level("L1", last_l1_top, last_l1_bot)

            # Check if currently inside any mathematical killzone
            in_killzone = False
            active_levels = [0.0] + config.get("levels", [0.5, 0.75]) + [1.0]
            structures_to_check = []

            if last_l1_top and last_l1_bot: structures_to_check.append(("L1", last_l1_top, last_l1_bot))
            if last_l2_top and last_l2_bot: structures_to_check.append(("L2", last_l2_top, last_l2_bot))

            killzone_text = "⏳ <i>Price outside Killzones.</i>"

            for struct_name, s_top, s_bot in structures_to_check:
                range_high, range_low, is_bull, is_slide = scanner._get_active_range(s_top, s_bot, orchestrator, current_candle)
                s_range = range_high - range_low

                for level in active_levels:
                    target_price = range_high - (s_range * level) if is_bull else range_low + (s_range * level)
                    expected_dir = "Bearish" if level == 0.0 else "Bullish" if is_bull else "Bullish" if level == 0.0 else "Bearish"

                    if scanner._is_in_killzone(current_candle, target_price, current_atr):
                        in_killzone = True
                        if level == 0.0: zone_label = "Range Top" if is_bull else "Range Bottom"
                        elif level == 1.0: zone_label = "Range Bottom" if is_bull else "Range Top"
                        else: zone_label = f"{level*100}% EQ"

                        struct_label = f"Dynamic {struct_name}" if is_slide else f"Static {struct_name}"
                        active_zone_label = f"{struct_label} {zone_label}"

                        ha_cache = scanner._calculate_ha(candle_cache)
                        if len(ha_cache) >= 2:
                            c2, c3 = ha_cache[-2], ha_cache[-1]
                            is_bullish_bounce = (expected_dir == "Bullish")

                            killzone_text = (f"⚠️ <b>Killzone:</b> [{active_zone_label}]\n"
                                             f"<code>{scanner._ha_desc(c2)} -> {scanner._ha_desc(c3)}</code>\n")

                            if scanner._check_ha_reversal(ha_cache, is_bullish_bounce):
                                killzone_text += f"✅ <b>Valid {expected_dir} Reversal Fired!</b>"
                            else:
                                killzone_text += f"⏳ <i>Waiting for {expected_dir} confirmation...</i>"
                        break
                if in_killzone: break

            block += killzone_text + "</blockquote>\n"
            return block
        except Exception as e:
            logger.error(f"Error generating snapshot for {symbol}: {e}")
            return f"📊 <b>{symbol}</b> | ❌ <i>Snapshot error.</i>\n\n"

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

                start_time = time.perf_counter()

                # --- REMOTE DEBUGGER GATEKEEPER CHECK ---
                should_dispatch_snapshot = False
                snapshot_msg = ""

                debugger_state = self.state_manager.state.get("debugger", {})
                if debugger_state.get("status") == "ON":
                    interval_str = debugger_state.get("interval", "15m")
                    tf_minutes = {'3m': 3, '15m': 15, '1h': 60, '4h': 240}.get(interval_str, 15)

                    current_dt = datetime.utcnow()
                    total_minutes = current_dt.hour * 60 + current_dt.minute
                    last_debug_run = self.state_manager.state.get("last_debug_run", -1)

                    # Ensure we only fire exactly once at the requested interval boundary
                    if total_minutes % tf_minutes == 0 and total_minutes != last_debug_run:
                        should_dispatch_snapshot = True
                        self.state_manager.state["last_debug_run"] = total_minutes

                        snapshot_msg = (f"🛠️ <b>OMNI-SENTINEL REMOTE DEBUGGER</b>\n"
                                        f"<i>Snapshot Interval: {interval_str} | {current_dt.strftime('%H:%M')} UTC</i>\n"
                                        f"━━━━━━━━━━━━━━━━━━━━\n\n")

                # 1. Pull the dynamic watchlist and build requests
                watchlist = self.state_manager.get_watchlist()
                fetch_requests: List[Tuple[str, str, str, str]] = []

                for composite_key, cfg in watchlist.items():
                    symbol = composite_key.split(':')[0] if ':' in composite_key else composite_key
                    m_type = cfg.get("type", "crypto")
                    tf = cfg.get("timeframe", "3m")
                    fetch_requests.append((composite_key, symbol, m_type, tf))

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

                    batch_l1_pivots = []
                    batch_l2_pivots = []

                    for idx, row in new_candles.iterrows():
                        current_candle = {
                            "timestamp": idx, "open": float(row['open']), "high": float(row['high']),
                            "low": float(row['low']), "close": float(row['close'])
                        }

                        current_cache = self.state_manager.update_candle_cache(composite_key, current_candle)

                        if len(current_cache) >= 14:
                            recent_highs = [c['high'] for c in current_cache[-14:]]
                            recent_lows = [c['low'] for c in current_cache[-14:]]
                            current_atr = (sum(h - l for h, l in zip(recent_highs, recent_lows)) / 14.0) * 1.0
                        else:
                            current_atr = (current_candle['high'] - current_candle['low']) * 1.0

                        l0, l1_list, l2_list = orchestrator.process_candle(
                            current_candle['high'], current_candle['low'], current_candle['timestamp'], current_atr
                        )

                        if l1_list: batch_l1_pivots.extend(l1_list)
                        if l2_list: batch_l2_pivots.extend(l2_list)

                        # Scan for alerts ONLY on the absolute newest (last) candle in the catch-up batch
                        if idx == new_candles.index[-1]:
                            symbol_alerts = scanner.scan(
                                composite_key, current_candle, current_cache, orchestrator,
                                batch_l1_pivots, batch_l2_pivots, self.state_manager
                            )

                            logger.info(f"   -> {composite_key} analyzed. Current Close: {current_candle['close']}")

                            if symbol_alerts:
                                all_alerts.extend([f"<b>{composite_key}</b> | {a}" for a in symbol_alerts])

                            # --- COMPILE DEBUGGER SNAPSHOT (If triggered) ---
                            if should_dispatch_snapshot:
                                snapshot_msg += self._build_snapshot_block(
                                    composite_key, current_candle, current_cache, orchestrator, config
                                )

                # 4. Dispatch Standard Alerts
                for alert in all_alerts:
                    await self.telegram.send_alert(alert)

                # 5. Dispatch Debugger Snapshot
                if should_dispatch_snapshot and snapshot_msg:
                    await self.telegram.send_alert(snapshot_msg)

                self.state_manager.save_state()

                end_time = time.perf_counter()
                loop_duration = round(end_time - start_time, 2)
                self.state_manager.state["last_loop_time"] = loop_duration
                logger.info(f"⏱️ Cycle completed in {loop_duration}s")

            except Exception as e:
                logger.error(f"❌ Critical error in trading loop: {e}", exc_info=True)
                await asyncio.sleep(10)

    async def run_concurrently(self) -> None:
        """
        Purpose:
            Initializes the Telegram bot and runs the trading loop alongside it asynchronously.
        """
        logger.info("🚀 Omni-Market Sentinel Starting...")

        await self.telegram.app.initialize()
        await self.telegram.app.start()
        await self.telegram.app.updater.start_polling()

        logger.info("📱 Telegram CLI Online. Type /help in your chat.")

        await self.preload_history()

        trading_task = asyncio.create_task(self.trading_loop())

        try:
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