"""
Purpose:
    The two-way Telegram CLI. Utilizes Interactive Inline Keyboards and State Machines
    to provide a seamless, button-driven mobile UX.
"""
import logging
import os
import pandas as pd
import psutil
from typing import List

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, BotCommand
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    filters,
    ContextTypes
)
from telegram.error import TelegramError

from app.config import Config
from app.state import StateManager
from app.visualizer import Visualizer

logger = logging.getLogger(__name__)


class TelegramCLI:
    """
    Purpose:
        Handles all inbound and outbound Telegram communications.
        Implements a state machine to track multi-step user interactions (like adding a pair).
    """

    def __init__(self, state_manager: StateManager):
        """
        Initializes the CLI, hooks up the state manager, and registers command/callback routers.
        """
        self.state_manager = state_manager
        self.chat_id = Config.TELEGRAM_CHAT_ID

        # Initialize the Application with a post_init hook to push the native autocomplete Menu
        self.app = Application.builder().token(Config.TELEGRAM_BOT_TOKEN).build()

        # Command Routers (Standard /commands)
        self.app.add_handler(CommandHandler("start", self.cmd_help))
        self.app.add_handler(CommandHandler("help", self.cmd_help))
        self.app.add_handler(CommandHandler("status", self.cmd_status))
        self.app.add_handler(CommandHandler("add", self.cmd_add))
        self.app.add_handler(CommandHandler("remove", self.cmd_remove))
        self.app.add_handler(CommandHandler("levels", self.cmd_levels))
        self.app.add_handler(CommandHandler("toggle", self.cmd_toggle))
        self.app.add_handler(CommandHandler("chart", self.cmd_chart))
        self.app.add_handler(CommandHandler("stats", self.cmd_stats))

        # Interactive UI Routers (Button Clicks)
        self.app.add_handler(CallbackQueryHandler(self.handle_button_click))

        # Text Input Router (State Machine input capture, ignores commands)
        self.app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, self.handle_text_input))

    # --- OUTBOUND DISPATCHER ---
    async def send_alert(self, message: str) -> None:
        """
        Purpose:
            Called by the main loop to dispatch trading alerts to the user.
        """
        if not self.chat_id:
            return

        try:
            await self.app.bot.send_message(
                chat_id=self.chat_id,
                text=message,
                parse_mode='HTML',
                disable_web_page_preview=True
            )
        except TelegramError as e:
            logger.error(f"❌ Failed to send Telegram alert: {e}")

    # --- UI HELPERS ---
    def _get_watchlist_keyboard(self, action_prefix: str) -> InlineKeyboardMarkup:
        """
        Purpose:
            Generates a dynamic inline keyboard based on the currently active watchlist.

        Args:
            action_prefix (str): The routing string prepended to the callback data.

        Returns:
            InlineKeyboardMarkup: The rendered Telegram keyboard object.
        """
        watchlist = self.state_manager.get_watchlist()
        keyboard = []
        for symbol in watchlist.keys():
            # The callback_data max length is 64 bytes. Format: "prefix|composite_symbol"
            keyboard.append([InlineKeyboardButton(f"📊 {symbol}", callback_data=f"{action_prefix}|{symbol}")])
        return InlineKeyboardMarkup(keyboard)

    # --- COMMANDS ---
    async def cmd_help(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        help_text = "🤖 <b>Omni-Market Sentinel</b>\n\nUse the blue <b>Menu</b> button below to navigate, or click a command:\n\n/status - View Active Watchlist\n/add - Track a new pair\n/chart - View a structure chart"
        await update.message.reply_text(help_text, parse_mode='HTML')

    async def cmd_status(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        watchlist = self.state_manager.get_watchlist()
        if not watchlist:
            await update.message.reply_text("📭 Watchlist is currently empty.")
            return

        msg = "📊 <b>Active Watchlist:</b>\n\n"
        for symbol, data in watchlist.items():
            levels = ", ".join(str(l) for l in data['levels'])
            alerts = data['alerts']
            bos = "✅" if alerts['bos'] else "❌"
            rev = "✅" if alerts['reversal'] else "❌"
            piv = "✅" if alerts['pivot'] else "❌"

            msg += (f"🔹 <b>{symbol}</b> ({data.get('type', 'crypto')})\n"
                    f"   Levels: {levels}\n"
                    f"   Alerts: BOS {bos} | Rev {rev} | Piv {piv}\n\n")

        await update.message.reply_text(msg, parse_mode='HTML')

    async def cmd_add(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        keyboard = [
            [InlineKeyboardButton("🪙 Crypto", callback_data="add_type|crypto"),
             InlineKeyboardButton("💱 Forex/Indices", callback_data="add_type|forex")]
        ]
        await update.message.reply_text("What type of market are you adding?", reply_markup=InlineKeyboardMarkup(keyboard))

    async def cmd_remove(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not self.state_manager.get_watchlist():
            await update.message.reply_text("📭 Watchlist is empty.")
            return
        await update.message.reply_text("Select the pair to remove:", reply_markup=self._get_watchlist_keyboard("remove_exec"))

    async def cmd_levels(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not self.state_manager.get_watchlist():
            await update.message.reply_text("📭 Watchlist is empty.")
            return
        await update.message.reply_text("Select the pair to update levels for:", reply_markup=self._get_watchlist_keyboard("levels_pair"))

    async def cmd_toggle(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not self.state_manager.get_watchlist():
            await update.message.reply_text("📭 Watchlist is empty.")
            return
        await update.message.reply_text("Select the pair to toggle alerts for:", reply_markup=self._get_watchlist_keyboard("toggle_pair"))

    async def cmd_chart(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not self.state_manager.get_watchlist():
            await update.effective_message.reply_text("📭 Watchlist is empty.")
            return
        await update.effective_message.reply_text("Select a pair to chart:", reply_markup=self._get_watchlist_keyboard("chart_exec"))

    async def cmd_stats(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        try:
            process = psutil.Process(os.getpid())
            ram_mb = process.memory_info().rss / (1024 * 1024)
            cpu_usage = process.cpu_percent(interval=0.1)
            last_loop_time = self.state_manager.state.get("last_loop_time", 0)
            watchlist_size = len(self.state_manager.get_watchlist())
            total_candles = sum(len(cache) for cache in self.state_manager.state.get("candle_caches", {}).values())

            msg = (f"🖥️ <b>System Diagnostics</b>\n\n"
                   f"🧠 <b>RAM Usage:</b> {ram_mb:.2f} MB\n"
                   f"⚙️ <b>CPU Load:</b> {cpu_usage}%\n"
                   f"⏱️ <b>Loop Latency:</b> {last_loop_time}s\n\n"
                   f"🗃️ <b>Active Pairs:</b> {watchlist_size}\n"
                   f"🗃️ <b>Cached Candles:</b> {total_candles}")
            await update.message.reply_text(msg, parse_mode='HTML')
        except Exception as e:
            logger.error(f"Error fetching stats: {e}")
            await update.message.reply_text("❌ Failed to retrieve system diagnostics.")


    # --- THE BUTTON ROUTER ---
    async def handle_button_click(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """
        Purpose:
            Catches all Inline Keyboard button presses and routes them to the correct logic
            based on the action prefix.
        """
        query = update.callback_query
        await query.answer()  # Acknowledge the button click to Telegram

        try:
            data = query.data.split("|")
            action = data[0]

            # STEP 1 of ADD: Type Selected -> Ask for Timeframe
            if action == "add_type":
                m_type = data[1]
                keyboard = [
                    [InlineKeyboardButton("3 min", callback_data=f"add_tf|{m_type}|3m"),
                     InlineKeyboardButton("15 min", callback_data=f"add_tf|{m_type}|15m"),
                     InlineKeyboardButton("1 hour", callback_data=f"add_tf|{m_type}|1h")],
                    [InlineKeyboardButton("4 hour", callback_data=f"add_tf|{m_type}|4h"),
                     InlineKeyboardButton("1 Day", callback_data=f"add_tf|{m_type}|1d")]
                ]
                await query.edit_message_text(f"Selected: {m_type.upper()}.\nNow choose the Timeframe:", reply_markup=InlineKeyboardMarkup(keyboard))

            # STEP 2 of ADD: Timeframe Selected -> Set State & Await Text Input
            elif action == "add_tf":
                m_type, tf = data[1], data[2]

                # Update the state machine context
                context.user_data['action'] = 'awaiting_add_symbol'
                context.user_data['add_type'] = m_type
                context.user_data['add_tf'] = tf

                example = "BTC/USDT" if m_type == "crypto" else "US30_USD"
                await query.edit_message_text(f"Almost done.\n\nPlease type the symbol you want to add.\n<i>Example: {example}</i>", parse_mode='HTML')

            # REMOVE EXECUTION
            elif action == "remove_exec":
                symbol = data[1]
                if self.state_manager.remove_symbol(symbol):
                    await query.edit_message_text(f"🗑️ <b>{symbol}</b> removed from scanner.", parse_mode='HTML')

            # TOGGLE: Pair Selected -> Ask for Alert Type
            elif action == "toggle_pair":
                symbol = data[1]
                keyboard = [
                    [InlineKeyboardButton("BOS", callback_data=f"toggle_exec|{symbol}|bos"),
                     InlineKeyboardButton("Reversals", callback_data=f"toggle_exec|{symbol}|reversal"),
                     InlineKeyboardButton("Pivots", callback_data=f"toggle_exec|{symbol}|pivot")]
                ]
                await query.edit_message_text(f"Toggle alerts for <b>{symbol}</b>:", reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='HTML')

            # TOGGLE EXECUTION
            elif action == "toggle_exec":
                symbol, alert_type = data[1], data[2]
                new_status = self.state_manager.toggle_alert(symbol, alert_type)
                await query.edit_message_text(f"🔄 <b>{alert_type.upper()}</b> alerts for {symbol} are now <b>{new_status}</b>.", parse_mode='HTML')

            # LEVELS: Pair Selected -> Set State & Await Text Input
            elif action == "levels_pair":
                symbol = data[1]
                context.user_data['action'] = 'awaiting_levels'
                context.user_data['level_symbol'] = symbol
                await query.edit_message_text(f"Please type the new EQ levels for <b>{symbol}</b> separated by a comma.\n<i>Example: 0.5, 0.75</i>", parse_mode='HTML')

            # CHART EXECUTION
            elif action == "chart_exec":
                symbol = data[1]
                await query.message.delete() # Remove the menu to keep chat clean
                await self._execute_chart(update.effective_message, symbol)

        except Exception as e:
            logger.error(f"Error handling button click: {e}")
            await query.message.reply_text("❌ An error occurred processing your request.")

    # --- THE TEXT INPUT ROUTER (State Machine) ---
    async def handle_text_input(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """
        Purpose:
            Catches free-form text input and evaluates it against the current state machine action.
        """
        action = context.user_data.get('action')
        text = update.message.text.upper().strip()

        try:
            # Handle Symbol Addition Execution
            if action == 'awaiting_add_symbol':
                m_type = context.user_data.get('add_type')
                tf = context.user_data.get('add_tf')

                # Construct the Composite Key!
                composite_symbol = f"{text}:{tf}"

                success = self.state_manager.add_symbol(composite_symbol, m_type)
                if not success:
                    await update.message.reply_text(f"⚠️ {composite_symbol} is already in the watchlist.")
                else:
                    # Persist the timeframe into the state config for the DataManager to use later
                    watchlist = self.state_manager.get_watchlist()
                    watchlist[composite_symbol]["timeframe"] = tf
                    self.state_manager.save_state()

                    status_msg = await update.message.reply_text(f"⏳ Downloading {tf} history for {composite_symbol}...")

                    # --- Dynamic Pre-loader ---
                    from app.data_manager import DataManager
                    temp_dm = DataManager()

                    if m_type == 'crypto':
                        market_data = await temp_dm.fetch_mexc_crypto(text, timeframe=tf, limit=400)
                    else:
                        market_data = await temp_dm.fetch_oanda_forex(text, timeframe=tf, limit=400)

                    if market_data is not None and not market_data.empty:

                        # --- THE FIX: LIVE CANDLE GATEKEEPER ---
                        # Drop the currently forming candle so the vault only learns from closed data
                        current_time_naive = pd.Timestamp.now('UTC').tz_localize(None)
                        market_data.index = pd.to_datetime(market_data.index).tz_localize(None)
                        time_diff_seconds = (current_time_naive - market_data.index[-1]).total_seconds()

                        tf_seconds_map = {'3m': 180, '15m': 900, '1h': 3600, '4h': 14400, '1d': 86400}
                        required_duration = tf_seconds_map.get(tf, 180)

                        if time_diff_seconds < required_duration:
                            market_data = market_data.iloc[:-1]
                        # ---------------------------------------

                        orchestrator = self.state_manager.get_orchestrator(composite_symbol)
                        cache = []

                        for index, row in market_data.iterrows():
                            current_candle = {
                                "timestamp": index,
                                "open": float(row['open']),
                                "high": float(row['high']),
                                "low": float(row['low']),
                                "close": float(row['close'])
                            }
                            cache.append(current_candle)

                            # Expand the ATR calculation for PEP 8 readability
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

                        self.state_manager.state["candle_caches"][composite_symbol] = cache[-150:]
                        self.state_manager.save_state()
                        await status_msg.edit_text(f"✅ <b>{composite_symbol}</b> added and mapped! History loaded.", parse_mode='HTML')
                    else:
                        # Clean up failed additions
                        self.state_manager.remove_symbol(composite_symbol)
                        await status_msg.edit_text(f"❌ Error: Could not fetch data for {text} on {tf}. Removed from watchlist.")

                # Clear State Machine
                context.user_data.clear()

            # Handle EQ Level Updates Execution
            elif action == 'awaiting_levels':
                symbol = context.user_data.get('level_symbol')
                try:
                    levels_float = [float(l.strip()) for l in text.split(',')]
                    # Ensure levels are statistically valid percentages
                    if not all(0 < l < 1 for l in levels_float):
                        raise ValueError("Levels must be between 0 and 1.")

                    if self.state_manager.update_levels(symbol, levels_float):
                        await update.message.reply_text(f"✅ Levels for <b>{symbol}</b> updated to: {levels_float}", parse_mode='HTML')
                    else:
                        await update.message.reply_text(f"⚠️ {symbol} not found.")
                except ValueError:
                    await update.message.reply_text("❌ <b>Error:</b> Invalid format. Levels must be decimals between 0 and 1 separated by commas. (e.g. 0.5, 0.75)", parse_mode='HTML')

                # Clear State Machine
                context.user_data.clear()

        except Exception as e:
            logger.error(f"Error handling text input: {e}")
            await update.message.reply_text("❌ An unexpected error occurred while processing your input.")
            context.user_data.clear() # Reset state to prevent user lock-in

    # --- HELPER LOGIC FOR CHARTING ---
    async def _execute_chart(self, message, symbol: str):
        """
        Purpose:
            Extracts the chart generation logic to keep the router clean.
            Retrieves data, triggers the visualizer, and dispatches the PNG.
        """
        try:
            cache = self.state_manager.state["candle_caches"].get(symbol)
            if not cache or len(cache) < 5:
                await message.reply_text(f"⚠️ Not enough data cached for <b>{symbol}</b> yet.", parse_mode='HTML')
                return

            status_msg = await message.reply_text(f"⏳ Generating structural chart for {symbol}...")

            # Convert cache to DataFrame for mplfinance
            df = pd.DataFrame(cache)
            df.set_index('timestamp', inplace=True)

            orchestrator = self.state_manager.get_orchestrator(symbol)
            viz = Visualizer()

            # Clean composite keys (e.g., BTC/USDT:15m -> BTC_USDT_15m) for safe file saving
            safe_symbol = symbol.replace("/", "_").replace(":", "_")
            filepath = f"data/{safe_symbol}_mobile.png"

            os.makedirs('data', exist_ok=True)
            viz.generate_static_chart(
                df,
                orchestrator.l1_logic.confirmed_pivots,
                orchestrator.l2_logic.confirmed_pivots,
                filepath
            )

            # Dispatch image and clean up local file
            if os.path.exists(filepath):
                with open(filepath, 'rb') as photo_file:
                    await message.reply_photo(
                        photo=photo_file,
                        caption=f"📊 <b>{symbol}</b> Market Structure\n<i>(Static Mobile View)</i>",
                        parse_mode='HTML'
                    )
                os.remove(filepath)
                await status_msg.delete()
            else:
                await status_msg.edit_text(f"❌ Failed to generate chart image.")

        except Exception as e:
            logger.error(f"Error executing chart: {e}")
            await message.reply_text("❌ An error occurred generating the chart.")