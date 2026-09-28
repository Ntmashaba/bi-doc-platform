"""Detected relationships between Data Factory activities and Power BI sources."""

from .rules import RULE_VERSION, Document, detect

__version__ = "0.1.0"
__all__ = ["RULE_VERSION", "Document", "detect"]
