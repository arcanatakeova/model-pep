"""
Core — Shared infrastructure for all trader subsystems.
"""
from .base_trader import BaseTrader
from .state_manager import StateManager
from .logging_setup import setup_logging

__all__ = ["BaseTrader", "StateManager", "setup_logging"]
