"""
Purpose:
    Analyzes live market data against historical L1/L2 structures to detect
    Pivot Formations, Break of Structure (BOS), Reversals, and Equilibrium touches.
    Utilizes a Sliding Window to track dynamic breakouts in real-time.
"""
import logging
from typing import List, Dict, Optional
from app.structure import MarketStructureOrchestrator, Pivot
from app.state import StateManager

logger = logging.getLogger(__name__)


class PatternScanner:
    def __init__(self, config: Dict):
        """
        Initializes the scanner with user-defined tracking rules.
        Example Config:
        {
            "levels": [0.5, 0.75],
            "alerts": {"bos": True, "reversal": True, "pivot": True}
        }
        """
        self.config = config

    def scan(self, symbol: str, current_candle: dict, candle_cache: List[dict],
             orchestrator: MarketStructureOrchestrator,
             newly_formed_l1: List[Pivot], newly_formed_l2: List[Pivot],
             state_manager: StateManager) -> List[str]:
        """
        Purpose:
            Runs all pattern checks on the current candle and returns a list of formatted alert strings.
        """
        alerts = []
        alerts_config = self.config.get("alerts", {})
        current_time = current_candle['timestamp'].timestamp() if hasattr(current_candle['timestamp'], 'timestamp') else \
            current_candle['timestamp']

        # Calculate live ATR for the Killzone buffer (using 1.0 multiplier as requested)
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

        # Extract the most recent confirmed L1 and L2 Pivots
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

            # --- 3. THE KILLZONE GATEKEEPER & REVERSALS (Sliding Window) ---
            if alerts_config.get("reversal", True):
                active_levels = [0.0] + self.config.get("levels", [0.5, 0.75]) + [1.0]

                structures_to_check = []
                if last_l1_top and last_l1_bot:
                    structures_to_check.append(("L1", last_l1_top, last_l1_bot))
                if last_l2_top and last_l2_bot:
                    structures_to_check.append(("L2", last_l2_top, last_l2_bot))

                for struct_name, s_top, s_bot in structures_to_check:
                    # Get the dynamic bounds for this specific structure
                    range_high, range_low, is_bullish_leg, is_sliding = self._get_active_range(s_top, s_bot,
                                                                                               orchestrator,
                                                                                               current_candle)
                    s_range = range_high - range_low

                    # --- INJECTED DEBUG LOGGING ---
                    leg_dir = "BULLISH (Bottom->Top)" if is_bullish_leg else "BEARISH (Top->Bottom)"
                    leg_state = "DYNAMIC" if is_sliding else "STATIC"

                    # Calculate EQ marks for the log
                    if is_bullish_leg:
                        eq_50 = range_high - (s_range * 0.5)
                        eq_75 = range_high - (s_range * 0.75)
                    else:
                        eq_50 = range_low + (s_range * 0.5)
                        eq_75 = range_low + (s_range * 0.75)

                    logger.info(
                        f"🔍 [DEBUG {symbol} {struct_name}] {leg_dir} | {leg_state} | "
                        f"Low: {range_low:.4f} -> High: {range_high:.4f} | "
                        f"50% EQ: {eq_50:.4f} | 75% EQ: {eq_75:.4f}"
                    )

                    for level in active_levels:
                        # Determine target price and expected bounce direction
                        if is_bullish_leg:
                            # Pulling back DOWN from the top
                            target_price = range_high - (s_range * level)
                            expected_dir = "Bearish" if level == 0.0 else "Bullish"
                        else:
                            # Pulling back UP from the bottom
                            target_price = range_low + (s_range * level)
                            expected_dir = "Bullish" if level == 0.0 else "Bearish"

                        # Check if inside this specific killzone
                        if self._is_in_killzone(current_candle, target_price, current_atr):

                            if level == 0.0:
                                zone_label = "Range Top" if is_bullish_leg else "Range Bottom"
                            elif level == 1.0:
                                zone_label = "Range Bottom" if is_bullish_leg else "Range Top"
                            else:
                                zone_label = f"{level*100}% EQ"

                            struct_label = f"Dynamic {struct_name}" if is_sliding else f"Static {struct_name}"
                            active_zone_label = f"[{struct_label} {zone_label}]"

                            # 1. Check Standard OHLC Reversal
                            ohlc_reversal = self._check_reversal(symbol, candle_cache, expected_dir)
                            if ohlc_reversal and state_manager.can_alert(symbol, f"OHLC_{struct_name}_{level}", current_time, cooldown_seconds=900):
                                alerts.append(f"{ohlc_reversal} | {symbol} at {active_zone_label}")

                            # 2. Check Heikin Ashi Reversal
                            ha_cache = self._calculate_ha(candle_cache)
                            is_bullish_bounce = (expected_dir == "Bullish")

                            # --- INJECTED HA DEBUG LOGGING ---
                            if len(ha_cache) >= 2:
                                c2, c3 = ha_cache[-2], ha_cache[-1]

                                def ha_desc(c):
                                    tot = c['high'] - c['low']
                                    body = abs(c['close'] - c['open'])
                                    perc = (body / tot * 100) if tot > 0 else 0
                                    color = "GREEN" if c['close'] > c['open'] else "RED"
                                    ctype = "DOJI" if perc <= 15.0 else "TREND"
                                    return f"{color} {ctype} ({perc:.1f}%)"

                                logger.info(
                                    f"[HA DEBUG {symbol} {active_zone_label}] "
                                    f"Prev: {ha_desc(c2)} | Latest: {ha_desc(c3)} | "
                                    f"Expected: {'Bounce UP' if is_bullish_bounce else 'Reject DOWN'}"
                                )

                            if self._check_ha_reversal(ha_cache, is_bullish_pullback=is_bullish_bounce):
                                dir_icon = "🟢" if is_bullish_bounce else "🔴"
                                if state_manager.can_alert(symbol, f"HA_{struct_name}_{level}", current_time, cooldown_seconds=900):
                                    alerts.append(f"{dir_icon} HA Momentum {expected_dir} Shift | {symbol} at {active_zone_label}")

                            break # Found our killzone, move to next structure

        return alerts

    def _get_active_range(self, last_top: Pivot, last_bot: Pivot, orchestrator: MarketStructureOrchestrator, current_candle: dict) -> tuple:
        """
        Solves the 'Broken Range' lag by implementing a Sliding Window.
        Returns: (range_high, range_low, is_bullish_leg, is_sliding)
        """
        # Scan L0 minor swings for the absolute extreme prices since the confirmed pivots
        max_high = current_candle['high']
        for p in orchestrator.l1_logic.lower_tops:
            if p.timestamp >= last_bot.timestamp and p.price > max_high:
                max_high = p.price

        min_low = current_candle['low']
        for p in orchestrator.l1_logic.lower_bottoms:
            if p.timestamp >= last_top.timestamp and p.price < min_low:
                min_low = p.price

        if last_top.timestamp > last_bot.timestamp:
            # Sequence: Bottom -> Top (Upward Leg)
            if max_high > last_top.price:
                # Top broken! Sliding UP. Anchor the bottom.
                return (max_high, last_bot.price, True, True)
            elif min_low < last_bot.price:
                # Bottom broken! Reversal. Sliding DOWN. Anchor the top.
                return (last_top.price, min_low, False, True)
            else:
                # Static Range.
                return (last_top.price, last_bot.price, True, False)
        else:
            # Sequence: Top -> Bottom (Downward Leg)
            if min_low < last_bot.price:
                # Bottom broken! Sliding DOWN. Anchor the top.
                return (last_top.price, min_low, False, True)
            elif max_high > last_top.price:
                # Top broken! Reversal. Sliding UP. Anchor the bottom.
                return (max_high, last_bot.price, True, True)
            else:
                # Static Range.
                return (last_top.price, last_bot.price, False, False)

    def _get_last_pivot(self, pivots: List[Pivot], type: int) -> Optional[Pivot]:
        """Helper to iterate backwards and find the most recent Top (1) or Bottom (-1)."""
        for p in reversed(pivots):
            if p.type == type:
                return p
        return None

    def _check_reversal(self, symbol: str, cache: List[dict], expected_dir: str) -> Optional[str]:
        """
        Evaluates the last 3 closed candles for engulfing reversal logic.
        Strictly filters out reversals that don't match the expected structural direction.
        """
        if len(cache) < 3:
            return None

        # C1 = Oldest, C2 = Middle, C3 = Newest (Just closed)
        c1, c2, c3 = cache[-3], cache[-2], cache[-1]

        # BULLISH REVERSAL (Downtrend Reversal)
        if expected_dir == "Bullish":
            if c2['low'] < c1['low'] and c2['low'] < c3['low']:
                # Relaxed: C3 just needs to close higher than C2's body
                if c3['close'] > max(c2['open'], c2['close']):
                    return f"🔄 Bullish Engulfing"

        # BEARISH REVERSAL (Uptrend Reversal)
        elif expected_dir == "Bearish":
            if c2['high'] > c1['high'] and c2['high'] > c3['high']:
                # Relaxed: C3 just needs to close lower than C2's body
                if c3['close'] < min(c2['open'], c2['close']):
                    return f"🔄 Bearish Engulfing"

        return None

    def _is_in_killzone(self, current_candle: dict, target_price: float, current_atr: float) -> bool:
        """
        Gatekeeper: Checks if the current candle's wicks touch the dynamic ATR zone around the target level.
        """
        upper_bound = target_price + current_atr
        lower_bound = target_price - current_atr
        return current_candle['low'] <= upper_bound and current_candle['high'] >= lower_bound

    def _calculate_ha(self, cache: List[dict]) -> List[dict]:
        """
        Math Engine: Converts a rolling window of standard OHLC candles into Heikin Ashi format.
        """
        ha_cache = []
        for i, c in enumerate(cache):
            # HA Close is always the average of the current OHLC
            ha_close = (c['open'] + c['high'] + c['low'] + c['close']) / 4.0

            if i == 0:
                ha_open = (c['open'] + c['close']) / 2.0
            else:
                # HA Open is the midpoint of the previous HA candle's body
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
        return ha_cache

    def _check_ha_reversal(self, ha_cache: List[dict], is_bullish_pullback: bool) -> bool:
        """
        Trigger: Evaluates the last 2 HA candles for Doji+Trend or Trend+Trend sequences.
        """
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