"""
Purpose:
    Manages the persistence of bot state (Market Structures, Candle Caches, Alert Histories).
    Ensures the bot can recover instantly from a crash without needing to re-process weeks of data.
    Implements file backups to prevent corruption during unexpected shutdowns.
"""
import os
import pickle
import shutil
import logging
from typing import Dict, List, Any
from app.structure import MarketStructureOrchestrator

logger = logging.getLogger(__name__)

class StateManager:
    def __init__(self, filepath: str = "data/bot_state.pkl"):
        """
        Initializes the state manager and defines the core memory schema.
        """
        self.filepath = filepath
        self.backup_path = f"{filepath}.bak"

        # Core memory schema
        self.state = {
            "orchestrators": {},  # type: Dict[str, MarketStructureOrchestrator]
            "candle_caches": {},  # type: Dict[str, List[dict]]
            "alert_states": {},   # type: Dict[str, float] -> Tracks timestamps for cooldowns
            "bos_records": {},    # type: Dict[str, Any] -> Tracks processed BOS pivot timestamps
            "pivot_records": {},  # type: Dict[str, Any] -> Tracks announced pivot formations
            "watchlist": {},      # type: Dict[str, Any] -> Dynamic tracking targets

            # --- THE REMOTE DEBUGGER ---
            # Default state initialization for fresh deployments
            "debugger": {
                "status": "OFF",
                "interval": "15m"
            }
        }

        # Ensure the data directory exists before attempting to load or save
        os.makedirs(os.path.dirname(self.filepath), exist_ok=True)
        self.load_state()

    def load_state(self) -> None:
        """
        Purpose:
            Loads the saved state from disk. If the main file is corrupted,
            it attempts to recover from the backup file.
            Also handles backward-compatibility for older save files.
        """
        if not os.path.exists(self.filepath):
            logger.info("[ℹ️] No existing state found. Starting fresh.")
            return

        try:
            with open(self.filepath, 'rb') as f:
                loaded_state = pickle.load(f)

            # Safely merge loaded state to preserve defaults for newly added keys
            for key, value in loaded_state.items():
                self.state[key] = value

            # --- BACKWARD COMPATIBILITY CHECK ---
            # If an old bot_state.pkl was loaded, it will overwrite the schema and might
            # be missing the "debugger" key. We must inject it safely here.
            if "debugger" not in self.state:
                self.state["debugger"] = {"status": "OFF", "interval": "15m"}
                logger.info("[⚙️] Upgraded existing state file with Remote Debugger schema.")

            logger.info(f"[💾] State loaded successfully from {self.filepath}")

        except (EOFError, pickle.UnpicklingError) as e:
            logger.warning(f"⚠️ Primary state file corrupted ({e}). Attempting to load backup...")
            if os.path.exists(self.backup_path):
                try:
                    with open(self.backup_path, 'rb') as f:
                        loaded_state = pickle.load(f)

                    # Apply the same safe merge and backward compatibility to the backup
                    for key, value in loaded_state.items():
                        self.state[key] = value

                    if "debugger" not in self.state:
                        self.state["debugger"] = {"status": "OFF", "interval": "15m"}

                    logger.info(f"[💾] Backup state loaded successfully.")
                except Exception as backup_error:
                    logger.error(f"❌ Backup also corrupted ({backup_error}). Starting fresh.")
            else:
                logger.error("❌ No backup found. Starting fresh.")

    def save_state(self) -> None:
        """
        Purpose:
            Safely pickles the current memory to the disk. Creates a backup of the
            old state before overwriting, preventing data loss if interrupted during write.
        """
        try:
            # Create a backup of the existing file before overwriting
            if os.path.exists(self.filepath):
                shutil.copy(self.filepath, self.backup_path)

            with open(self.filepath, 'wb') as f:
                pickle.dump(self.state, f)
        except Exception as e:
            logger.error(f"❌ Critical error saving state: {e}")

    def get_orchestrator(self, symbol: str) -> MarketStructureOrchestrator:
        """Retrieves or initializes the Orchestrator for a specific symbol."""
        if symbol not in self.state["orchestrators"]:
            self.state["orchestrators"][symbol] = MarketStructureOrchestrator()
        return self.state["orchestrators"][symbol]

    def update_candle_cache(self, symbol: str, new_candle: dict) -> List[dict]:
        """
        Purpose:
            Maintains the rolling window. Max 150 candles.
            If it hits 150, drops the oldest 50, keeping the newest 100.
        """
        if symbol not in self.state["candle_caches"]:
            self.state["candle_caches"][symbol] = []

        cache = self.state["candle_caches"][symbol]
        cache.append(new_candle)

        # 150/50 Wipe Logic to maintain optimal memory footprint
        if len(cache) > 150:
            self.state["candle_caches"][symbol] = cache[-101:]

        return self.state["candle_caches"][symbol]

    def can_alert(self, symbol: str, alert_type: str, current_time: float, cooldown_seconds: int = 900) -> bool:
        """
        Purpose:
            Spam Protection. Checks if enough time has passed since the last alert of this type.
            Default cooldown is 15 minutes (900 seconds).
        """
        key = f"{symbol}_{alert_type}"
        last_alert_time = self.state["alert_states"].get(key, 0)

        # Always allow if it has never alerted before (0), OR if the cooldown has passed.
        if last_alert_time == 0 or (current_time - last_alert_time) >= cooldown_seconds:
            self.state["alert_states"][key] = current_time
            return True
        return False

    def has_bos_triggered(self, symbol: str, pivot_type: str, pivot_timestamp: float) -> bool:
        """Checks if a Break of Structure has already been alerted for a specific pivot."""
        if symbol not in self.state["bos_records"]:
            self.state["bos_records"][symbol] = {}
        return self.state["bos_records"][symbol].get(pivot_type) == pivot_timestamp

    def set_bos_triggered(self, symbol: str, pivot_type: str, pivot_timestamp: float) -> None:
        """Logs that a Break of Structure alert was sent for a specific pivot."""
        if symbol not in self.state["bos_records"]:
            self.state["bos_records"][symbol] = {}
        self.state["bos_records"][symbol][pivot_type] = pivot_timestamp

    def has_pivot_triggered(self, symbol: str, level: str, pivot_timestamp: float) -> bool:
        """Checks if a Pivot Formation has already been announced for a specific timestamp."""
        if "pivot_records" not in self.state:
            self.state["pivot_records"] = {}

        if symbol not in self.state["pivot_records"]:
            self.state["pivot_records"][symbol] = {}

        return self.state["pivot_records"][symbol].get(level) == pivot_timestamp

    def set_pivot_triggered(self, symbol: str, level: str, pivot_timestamp: float) -> None:
        """Logs that a Pivot Formation alert was sent to prevent duplicate announcements."""
        if "pivot_records" not in self.state:
            self.state["pivot_records"] = {}

        if symbol not in self.state["pivot_records"]:
            self.state["pivot_records"][symbol] = {}

        self.state["pivot_records"][symbol][level] = pivot_timestamp

    def get_watchlist(self) -> Dict[str, Any]:
        """
        Returns the current dynamic watchlist.
        Provides a default starter list if completely empty on first boot.
        """
        if "watchlist" not in self.state or not self.state["watchlist"]:
            self.state["watchlist"] = {
                "BTC/USDT:15m": {"type": "crypto", "timeframe": "15m", "levels": [0.5, 0.75],
                             "alerts": {"bos": True, "reversal": True, "pivot": False}},
                "SOL/USDT:15m": {"type": "crypto", "timeframe": "15m", "levels": [0.5, 0.75],
                             "alerts": {"bos": True, "reversal": True, "pivot": True}}
            }
        return self.state["watchlist"]

    def add_symbol(self, symbol: str, market_type: str) -> bool:
        """Adds a new composite symbol with default tracking settings."""
        watchlist = self.get_watchlist()
        if symbol in watchlist:
            return False  # Already exists

        watchlist[symbol] = {
            "type": market_type,
            "levels": [0.5, 0.75],
            "alerts": {"bos": True, "reversal": True, "pivot": False}
        }
        self.save_state()
        return True

    def remove_symbol(self, symbol: str) -> bool:
        """Removes a symbol from the active scanner and deletes its memory cache."""
        watchlist = self.get_watchlist()
        if symbol in watchlist:
            del watchlist[symbol]

            # Clean up associated memory to prevent vault bloat
            self.state["orchestrators"].pop(symbol, None)
            self.state["candle_caches"].pop(symbol, None)

            self.save_state()
            return True
        return False

    def update_levels(self, symbol: str, levels: List[float]) -> bool:
        """Updates the equilibrium tracking levels for a symbol."""
        watchlist = self.get_watchlist()
        if symbol in watchlist:
            watchlist[symbol]["levels"] = levels
            self.save_state()
            return True
        return False

    def toggle_alert(self, symbol: str, alert_type: str) -> str:
        """Toggles a specific alert on/off and returns the new state."""
        watchlist = self.get_watchlist()
        if symbol in watchlist and alert_type in watchlist[symbol]["alerts"]:
            current_state = watchlist[symbol]["alerts"][alert_type]
            new_state = not current_state
            watchlist[symbol]["alerts"][alert_type] = new_state
            self.save_state()
            return "ON" if new_state else "OFF"
        return "ERROR"