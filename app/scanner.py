"""
Purpose:
    Analyzes live market data against historical L1/L2 structures to detect
    Pivot Formations, Break of Structure (BOS), Reversals, and Equilibrium touches.
    Utilizes a Sliding Window to track dynamic breakouts in real-time.
"""
import logging
from typing import List, Dict, Optional, Tuple
from app.structure import MarketStructureOrchestrator, Pivot
from app.state import StateManager

logger = logging.getLogger(__name__)


class PatternScanner:
    def __init__(self, config: Dict):
        """
        Initializes the scanner with user-defined tracking rules.

        Args:
            config (Dict): The configuration dictionary containing tracking levels and alert toggles.

        Returns:
            None

        Example:
            config = {"levels": [0.5, 0.75], "alerts": {"bos": True, "reversal": True, "pivot": True}}
            scanner = PatternScanner(config)
        """
        self.config = config

    def _ha_desc(self, c: dict) -> str:
        """
        Helper to format Heikin Ashi candle descriptions for the terminal log.
        Calculates the body-to-total-range percentage to classify as DOJI or TREND.
        """
        try:
            tot = c['high'] - c['low']
            body = abs(c['close'] - c['open'])
            perc = (body / tot * 100) if tot > 0 else 0
            color = "GREEN" if c['close'] > c['open'] else "RED"
            ctype = "DOJI" if perc <= 15.0 else "TREND"
            return f"{color} {ctype} ({perc:.1f}%)"
        except Exception as e:
            logger.error(f"Error formatting HA description: {e}")
            return "UNKNOWN"

    def scan(self, symbol: str, current_candle: dict, candle_cache: List[dict],
             orchestrator: MarketStructureOrchestrator,
             newly_formed_l1: List[Pivot], newly_formed_l2: List[Pivot],
             state_manager: StateManager) -> List[str]:
        """
        Runs all pattern checks on the current candle and returns a list of formatted alert strings.
        Compiles a clean, single-block debug log for the terminal to prevent spam.

        Args:
            symbol (str): The trading pair composite key (e.g., 'BTC/USDT:15m').
            current_candle (dict): The latest closed OHLCV data point.
            candle_cache (List[dict]): The rolling window of historical candles.
            orchestrator (MarketStructureOrchestrator): The structure brain containing L1/L2 pivots.
            newly_formed_l1 (List[Pivot]): Any L1 pivots formed on this specific tick.
            newly_formed_l2 (List[Pivot]): Any L2 pivots formed on this specific tick.
            state_manager (StateManager): The persistence layer for cooldowns and duplicate checks.

        Returns:
            List[str]: A list of formatted HTML alert strings to be dispatched to Telegram.
        """
        alerts = []
        try:
            alerts_config = self.config.get("alerts", {})

            # Extract timestamp safely depending on whether it's a Pandas object or raw float
            current_time = current_candle['timestamp'].timestamp() if hasattr(current_candle['timestamp'], 'timestamp') else current_candle['timestamp']

            # Start building the clean terminal log block
            debug_lines = [f"📊 {symbol} | Current Close: {current_candle['close']:.4f}"]

            # Calculate live ATR for the Killzone buffer
            if len(candle_cache) >= 14:
                recent_highs = [c['high'] for c in candle_cache[-14:]]
                recent_lows = [c['low'] for c in candle_cache[-14:]]
                current_atr = (sum(h - l for h, l in zip(recent_highs, recent_lows)) / 14.0) * 1.0
            else:
                current_atr = (current_candle['high'] - current_candle['low']) * 1.0

            # --- 1. PIVOT FORMATION ALERTS ---
            if alerts_config.get("pivot", True):
                for p in newly_formed_l1:
                    p_type = "TOP" if p.type == 1 else "BOTTOM"
                    if not state_manager.has_pivot_triggered(symbol, f"L1_{p_type}", p.timestamp):
                        alerts.append(f"🌟 L1 {p_type} Formed | {symbol} Price: {p.price:.4f}")
                        state_manager.set_pivot_triggered(symbol, f"L1_{p_type}", p.timestamp)

                for p in newly_formed_l2:
                    p_type = "TOP" if p.type == 1 else "BOTTOM"
                    if not state_manager.has_pivot_triggered(symbol, f"L2_{p_type}", p.timestamp):
                        alerts.append(f"👑 L2 {p_type} Formed | {symbol} Price: {p.price:.4f}")
                        state_manager.set_pivot_triggered(symbol, f"L2_{p_type}", p.timestamp)

            # Retrieve the most recent anchors for structure tracking
            last_l1_top = self._get_last_pivot(orchestrator.l1_logic.confirmed_pivots, type=1)
            last_l1_bot = self._get_last_pivot(orchestrator.l1_logic.confirmed_pivots, type=-1)
            last_l2_top = self._get_last_pivot(orchestrator.l2_logic.confirmed_pivots, type=1)
            last_l2_bot = self._get_last_pivot(orchestrator.l2_logic.confirmed_pivots, type=-1)

            if last_l1_top and last_l1_bot:
                # --- 2. BREAK OF STRUCTURE (BOS) ---
                if alerts_config.get("bos", True):
                    close_price = current_candle['close']
                    if close_price > last_l1_top.price:
                        if not state_manager.has_bos_triggered(symbol, "L1_TOP", last_l1_top.timestamp):
                            alerts.append(f"🚨 BOS (Bullish) | {symbol} closed above L1 Top at {last_l1_top.price:.4f}")
                            state_manager.set_bos_triggered(symbol, "L1_TOP", last_l1_top.timestamp)
                    elif close_price < last_l1_bot.price:
                        if not state_manager.has_bos_triggered(symbol, "L1_BOT", last_l1_bot.timestamp):
                            alerts.append(f"🚨 BOS (Bearish) | {symbol} closed below L1 Bottom at {last_l1_bot.price:.4f}")
                            state_manager.set_bos_triggered(symbol, "L1_BOT", last_l1_bot.timestamp)

                # --- 3. THE KILLZONE GATEKEEPER & REVERSALS ---
                if alerts_config.get("reversal", True):
                    # Inject 0.0 (Bottom) and 1.0 (Top) as active killzones alongside standard EQs
                    active_levels = [0.0] + self.config.get("levels", [0.5, 0.75]) + [1.0]

                    structures_to_check = []
                    if last_l1_top and last_l1_bot:
                        structures_to_check.append(("L1", last_l1_top, last_l1_bot))
                    if last_l2_top and last_l2_bot:
                        structures_to_check.append(("L2", last_l2_top, last_l2_bot))

                    for struct_name, s_top, s_bot in structures_to_check:
                        # Fetch the sliding window bounds
                        range_high, range_low, is_bullish_leg, is_sliding = self._get_active_range(s_top, s_bot, orchestrator, current_candle)
                        s_range = range_high - range_low

                        leg_dir = "BULLISH (Bot->Top)" if is_bullish_leg else "BEARISH (Top->Bot)"
                        leg_state = "DYNAMIC" if is_sliding else "STATIC"

                        # Calculate Equilibrium markers for the terminal log
                        if is_bullish_leg:
                            eq_50 = range_high - (s_range * 0.5)
                            eq_75 = range_high - (s_range * 0.75)
                        else:
                            eq_50 = range_low + (s_range * 0.5)
                            eq_75 = range_low + (s_range * 0.75)

                        # Append the clean structure overview to the log block
                        debug_lines.append(f"   {struct_name} [{leg_state} {leg_dir}]: {range_low:.4f} -> {range_high:.4f} | 50%: {eq_50:.4f} | 75%: {eq_75:.4f}")

                        for level in active_levels:
                            # Map the mathematical level to a directional expectation
                            if is_bullish_leg:
                                target_price = range_high - (s_range * level)
                                expected_dir = "Bearish" if level == 0.0 else "Bullish"
                            else:
                                target_price = range_low + (s_range * level)
                                expected_dir = "Bullish" if level == 0.0 else "Bearish"

                            # Pass the gatekeeper: Is price physically touching the ATR band of this level?
                            if self._is_in_killzone(current_candle, target_price, current_atr):

                                # Format human-readable labels for the alerts
                                if level == 0.0:
                                    zone_label = "Range Top" if is_bullish_leg else "Range Bottom"
                                elif level == 1.0:
                                    zone_label = "Range Bottom" if is_bullish_leg else "Range Top"
                                else:
                                    zone_label = f"{level*100}% EQ"

                                struct_label = f"Dynamic {struct_name}" if is_sliding else f"Static {struct_name}"
                                active_zone_label = f"{struct_label} {zone_label}"

                                # 1. Standard OHLC Reversal Check
                                ohlc_reversal = self._check_reversal(symbol, candle_cache, expected_dir)
                                if ohlc_reversal and state_manager.can_alert(symbol, f"OHLC_{struct_name}_{level}", current_time, cooldown_seconds=900):
                                    alerts.append(f"{ohlc_reversal} | {symbol} at [{active_zone_label}]")

                                # 2. Heikin Ashi Momentum Shift Check
                                ha_cache = self._calculate_ha(candle_cache)
                                if len(ha_cache) >= 2:
                                    c2, c3 = ha_cache[-2], ha_cache[-1]
                                    is_bullish_bounce = (expected_dir == "Bullish")

                                    # Log the current HA sequence state internally
                                    debug_lines.append(f"      ⚠️ In Zone [{active_zone_label}]: {self._ha_desc(c2)} -> {self._ha_desc(c3)}")

                                    # Trigger evaluation using the strict original math
                                    if self._check_ha_reversal(ha_cache, is_bullish_pullback=is_bullish_bounce):
                                        debug_lines.append(f"      ✅ Valid {expected_dir} Reversal Fired!")
                                        dir_icon = "🟢" if is_bullish_bounce else "🔴"
                                        if state_manager.can_alert(symbol, f"HA_{struct_name}_{level}", current_time, cooldown_seconds=900):
                                            alerts.append(f"{dir_icon} HA Momentum {expected_dir} Shift | {symbol} at [{active_zone_label}]")
                                    else:
                                        debug_lines.append(f"      ⏳ Waiting for {expected_dir} confirmation...")

                                break # Found the valid killzone, skip checking deeper levels for this structure

            # Output the fully assembled, clean log block to the terminal once per cycle
            logger.info("\n" + "\n".join(debug_lines))

        except Exception as e:
            logger.error(f"❌ Critical error scanning {symbol}: {e}", exc_info=True)

        return alerts

    def _get_active_range(self, last_top: Pivot, last_bot: Pivot, orchestrator: MarketStructureOrchestrator, current_candle: dict) -> Tuple[float, float, bool, bool]:
        """
        Calculates the active structural range, implementing a 'Sliding Window' to adjust
        for dynamic breakouts before a new opposing pivot is fully confirmed.

        Args:
            last_top (Pivot): The most recent confirmed Top.
            last_bot (Pivot): The most recent confirmed Bottom.
            orchestrator (MarketStructureOrchestrator): Contains the unconfirmed L0 swings.
            current_candle (dict): Used to catch immediate real-time breaks.

        Returns:
            Tuple[float, float, bool, bool]: (range_high, range_low, is_bullish_leg, is_sliding)
        """
        try:
            # Scan L0 minor swings for absolute extremes since the confirmed pivots
            max_high = current_candle['high']
            for p in orchestrator.l1_logic.lower_tops:
                if p.timestamp >= last_bot.timestamp and p.price > max_high:
                    max_high = p.price

            min_low = current_candle['low']
            for p in orchestrator.l1_logic.lower_bottoms:
                if p.timestamp >= last_top.timestamp and p.price < min_low:
                    min_low = p.price

            # Determine leg direction based on timestamp chronology
            if last_top.timestamp > last_bot.timestamp:
                # Sequence: Bottom -> Top (Upward Leg)
                if max_high > last_top.price:
                    return (max_high, last_bot.price, True, True)  # Sliding UP
                elif min_low < last_bot.price:
                    return (last_top.price, min_low, False, True)  # Reversal Sliding DOWN
                else:
                    return (last_top.price, last_bot.price, True, False)  # Static
            else:
                # Sequence: Top -> Bottom (Downward Leg)
                if min_low < last_bot.price:
                    return (last_top.price, min_low, False, True)  # Sliding DOWN
                elif max_high > last_top.price:
                    return (max_high, last_bot.price, True, True)  # Reversal Sliding UP
                else:
                    return (last_top.price, last_bot.price, False, False)  # Static
        except Exception as e:
            logger.error(f"Error calculating active range: {e}")
            return (last_top.price, last_bot.price, True, False) # Fallback to static

    def _get_last_pivot(self, pivots: List[Pivot], type: int) -> Optional[Pivot]:
        """
        Iterates backwards through the confirmed pivots list to find the most recent Top or Bottom.
        """
        for p in reversed(pivots):
            if p.type == type:
                return p
        return None

    def _check_reversal(self, symbol: str, cache: List[dict], expected_dir: str) -> Optional[str]:
        """
        Evaluates the last 3 closed standard OHLC candles for engulfing patterns.
        Filters out reversals that contradict the expected structural bounce direction.
        """
        try:
            if len(cache) < 3:
                return None

            c1, c2, c3 = cache[-3], cache[-2], cache[-1]

            if expected_dir == "Bullish":
                # Check for a lower low followed by a bullish engulfing close
                if c2['low'] < c1['low'] and c2['low'] < c3['low']:
                    if c3['close'] > max(c2['open'], c2['close']):
                        return f"🔄 Bullish Engulfing"
            elif expected_dir == "Bearish":
                # Check for a higher high followed by a bearish engulfing close
                if c2['high'] > c1['high'] and c2['high'] > c3['high']:
                    if c3['close'] < min(c2['open'], c2['close']):
                        return f"🔄 Bearish Engulfing"
            return None
        except Exception as e:
            logger.error(f"Error checking OHLC reversal: {e}")
            return None

    def _is_in_killzone(self, current_candle: dict, target_price: float, current_atr: float) -> bool:
        """
        Checks if the current candle's wicks intersect the dynamic ATR boundary of the target level.
        """
        upper_bound = target_price + current_atr
        lower_bound = target_price - current_atr
        return current_candle['low'] <= upper_bound and current_candle['high'] >= lower_bound

    def _calculate_ha(self, cache: List[dict]) -> List[dict]:
        """
        Converts a rolling window of standard OHLC candles into Heikin Ashi format.
        """
        ha_cache = []
        try:
            for i, c in enumerate(cache):
                ha_close = (c['open'] + c['high'] + c['low'] + c['close']) / 4.0
                if i == 0:
                    ha_open = (c['open'] + c['close']) / 2.0
                else:
                    prev_ha = ha_cache[i - 1]
                    ha_open = (prev_ha['open'] + prev_ha['close']) / 2.0
                ha_high = max(c['high'], ha_open, ha_close)
                ha_low = min(c['low'], ha_open, ha_close)

                ha_cache.append({
                    "open": ha_open,
                    "high": ha_high,
                    "low": ha_low,
                    "close": ha_close,
                    "timestamp": c.get("timestamp")
                })
        except Exception as e:
            logger.error(f"Error calculating HA: {e}")
        return ha_cache

    def _check_ha_reversal(self, ha_cache: List[dict], is_bullish_pullback: bool) -> bool:
        """
        Evaluates the last 2 HA candles for Doji+Trend or Trend+Trend sequences.
        Restored original logic to strictly enforce valid momentum shifts.
        """
        try:
            if len(ha_cache) < 2:
                return False

            c2 = ha_cache[-2]
            c3 = ha_cache[-1]

            def is_doji(c):
                body = abs(c['close'] - c['open'])
                total = c['high'] - c['low']
                # A Doji has a body less than or equal to 15% of its total range
                return total > 0 and body <= (total * 0.15)

            def is_green(c):
                return c['close'] > c['open']

            def is_red(c):
                return c['close'] < c['open']

            if is_bullish_pullback:
                # We pulled back DOWN to the EQ, looking for a bounce UP (Green)
                seq_a = is_doji(c2) and is_green(c3)
                seq_b = is_green(c2) and is_green(c3) and not is_doji(c2)
                return seq_a or seq_b
            else:
                # We pulled back UP to the EQ, looking for a rejection DOWN (Red)
                seq_a = is_doji(c2) and is_red(c3)
                seq_b = is_red(c2) and is_red(c3) and not is_doji(c2)
                return seq_a or seq_b
        except Exception as e:
            logger.error(f"Error checking HA reversal: {e}")
            return False