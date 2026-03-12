"""
Purpose:
    Implements the "NeuroTrader" Hierarchical Market Structure logic.
    - Level 0: Directional Change (Filters raw candle noise using ATR).
    - Level 1 & 2: Hierarchical Structure (Identifies Swing Highs/Lows and enforces Top/Bottom alternation).
"""
from typing import Optional, List, Tuple
from dataclasses import dataclass


@dataclass
class Pivot:
    """
    Purpose:
        A pure data structure representing a confirmed turning point in the market.
    """
    price: float
    timestamp: any
    type: int  # 1 for Top (High), -1 for Bottom (Low)
    level: int  # 0 for Base, 1 for Minor Structure, 2 for Major Structure


class DirectionalChange:
    """
    Purpose:
        Level 0 Logic. Finds the alternating baseline tops and bottoms using volatility (ATR).
        It establishes the base 'ZigZag' that higher levels will evaluate.
    """

    def __init__(self):
        self.direction = 0  # 0 = Undefined, 1 = Upward (Seeking Top), -1 = Downward (Seeking Bottom)
        self.pending_high = float('-inf')
        self.pending_high_time = None
        self.pending_low = float('inf')
        self.pending_low_time = None

    def process_candle(self, high: float, low: float, timestamp: any, atr: float) -> Optional[Pivot]:
        """
        Purpose:
            Evaluates a single candle to see if a Level 0 Pivot is confirmed via ATR pullback.
        """
        try:
            # Initialization: Set baseline on the very first candle
            if self.direction == 0:
                self.pending_high, self.pending_high_time = high, timestamp
                self.pending_low, self.pending_low_time = low, timestamp
                self.direction = 1
                return None

            # Upward Trend: Looking for a Top
            if self.direction == 1:
                # Keep tracking the highest point
                if high > self.pending_high:
                    self.pending_high, self.pending_high_time = high, timestamp

                # Check for pullback greater than the ATR threshold
                elif low < (self.pending_high - atr):
                    top = Pivot(price=self.pending_high, timestamp=self.pending_high_time, type=1, level=0)
                    self.direction = -1  # Switch to looking for a bottom
                    self.pending_low, self.pending_low_time = low, timestamp
                    return top

            # Downward Trend: Looking for a Bottom
            elif self.direction == -1:
                # Keep tracking the lowest point
                if low < self.pending_low:
                    self.pending_low, self.pending_low_time = low, timestamp

                # Check for bounce greater than the ATR threshold
                elif high > (self.pending_low + atr):
                    bottom = Pivot(price=self.pending_low, timestamp=self.pending_low_time, type=-1, level=0)
                    self.direction = 1  # Switch to looking for a top
                    self.pending_high, self.pending_high_time = high, timestamp
                    return bottom

            return None

        except Exception as e:
            print(f"Error processing Directional Change L0: {e}")
            return None


class HierarchicalStructure:
    """
    Purpose:
        Level 1 and Level 2 Logic. Upgrades lower-level pivots using the "Neighbor Comparison"
        method while strictly enforcing the rule that Tops and Bottoms must alternate.
    """

    def __init__(self, target_level: int):
        self.target_level = target_level
        self.lower_tops: List[Pivot] = []
        self.lower_bottoms: List[Pivot] = []
        self.confirmed_pivots: List[Pivot] = []  # Tracks the final output to enforce alternation

    def process_lower_pivot(self, pivot: Pivot) -> List[Pivot]:
        """
        Purpose:
            Takes a confirmed lower-level pivot and checks if it forms a valid higher-level structure.
            Returns a list because enforcing alternation might require plotting two points at once.
        """
        new_pivots = []

        try:
            # --- EVALUATE TOPS ---
            if pivot.type == 1:
                self.lower_tops.append(pivot)

                # We need at least 3 tops to compare the middle against the left and right
                if len(self.lower_tops) >= 3:
                    left, middle, right = self.lower_tops[-3], self.lower_tops[-2], self.lower_tops[-1]

                    # Neighbor Comparison: Is the middle top higher than its neighbors?
                    if middle.price > left.price and middle.price > right.price:
                        candidate = Pivot(middle.price, middle.timestamp, 1, self.target_level)

                        # ALTERNATION CHECK: If the last confirmed pivot was ALSO a Top...
                        if self.confirmed_pivots and self.confirmed_pivots[-1].type == 1:
                            last_top_time = self.confirmed_pivots[-1].timestamp
                            lowest_bot = None

                            # ...we must find the lowest bottom between the two tops to connect them.
                            for b in self.lower_bottoms:
                                if last_top_time <= b.timestamp <= candidate.timestamp:
                                    if lowest_bot is None or b.price < lowest_bot.price:
                                        lowest_bot = b

                            # Upgrade the missing bottom and add it to our list first
                            if lowest_bot:
                                missing_bot = Pivot(lowest_bot.price, lowest_bot.timestamp, -1, self.target_level)
                                self.confirmed_pivots.append(missing_bot)
                                new_pivots.append(missing_bot)

                        # Finally, append our candidate Top
                        self.confirmed_pivots.append(candidate)
                        new_pivots.append(candidate)

            # --- EVALUATE BOTTOMS ---
            elif pivot.type == -1:
                self.lower_bottoms.append(pivot)

                # We need at least 3 bottoms to compare
                if len(self.lower_bottoms) >= 3:
                    left, middle, right = self.lower_bottoms[-3], self.lower_bottoms[-2], self.lower_bottoms[-1]

                    # Neighbor Comparison: Is the middle bottom lower than its neighbors?
                    if middle.price < left.price and middle.price < right.price:
                        candidate = Pivot(middle.price, middle.timestamp, -1, self.target_level)

                        # ALTERNATION CHECK: If the last confirmed pivot was ALSO a Bottom...
                        if self.confirmed_pivots and self.confirmed_pivots[-1].type == -1:
                            last_bot_time = self.confirmed_pivots[-1].timestamp
                            highest_top = None

                            # ...we must find the highest top between the two bottoms to connect them.
                            for t in self.lower_tops:
                                if last_bot_time <= t.timestamp <= candidate.timestamp:
                                    if highest_top is None or t.price > highest_top.price:
                                        highest_top = t

                            # Upgrade the missing top and add it to our list first
                            if highest_top:
                                missing_top = Pivot(highest_top.price, highest_top.timestamp, 1, self.target_level)
                                self.confirmed_pivots.append(missing_top)
                                new_pivots.append(missing_top)

                        # Finally, append our candidate Bottom
                        self.confirmed_pivots.append(candidate)
                        new_pivots.append(candidate)

            return new_pivots

        except Exception as e:
            print(f"Error processing L{self.target_level}: {e}")
            return []


class MarketStructureOrchestrator:
    """
    Purpose:
        The Brain. Passes data through L0, and cascades results up to L1 and L2 sequentially.
    """

    def __init__(self):
        self.l0_logic = DirectionalChange()
        self.l1_logic = HierarchicalStructure(target_level=1)
        self.l2_logic = HierarchicalStructure(target_level=2)

    def process_candle(self, high: float, low: float, timestamp: any, atr: float) -> Tuple[
        Optional[Pivot], List[Pivot], List[Pivot]]:
        """Passes a single candle through the entire hierarchy."""
        l0_pivot = self.l0_logic.process_candle(high, low, timestamp, atr)
        l1_pivots = []
        l2_pivots = []

        if l0_pivot:
            # If L0 generates a pivot, pass it to L1
            l1_pivots = self.l1_logic.process_lower_pivot(l0_pivot)

            # If L1 generates any pivots (can be multiple due to alternation fixing), pass them to L2
            for l1 in l1_pivots:
                l2_pivots.extend(self.l2_logic.process_lower_pivot(l1))

        return l0_pivot, l1_pivots, l2_pivots