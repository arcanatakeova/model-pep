"""ARCANA AI — Specialized Scanner Suite.

Each scanner targets a specific platform with optimized queries,
rate-limit awareness, and platform-native response strategies.

The ScannerOrchestrator coordinates all scanners, distributes budget,
deduplicates across platforms, and routes results to the CRM pipeline.
"""

from src.scanners.scanner_orchestrator import ScannerOrchestrator

# Re-export the base scanner from opportunity_scanner for backward compatibility
from src.opportunity_scanner import OpportunityScanner

__all__ = [
    "ScannerOrchestrator",
    "OpportunityScanner",
]
