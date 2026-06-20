"""Data providers turn raw symbols into Snapshots for the screener."""

from .base import Provider
from .json_provider import JsonProvider

__all__ = ["Provider", "JsonProvider"]
