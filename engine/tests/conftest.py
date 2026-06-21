"""Shared pytest config.

Adds the vendored free-llm-router twin to sys.path so its pure-logic units
(token bucket, circuit breaker, provider ordering) can be tested directly
without a network or any provider keys.
"""
import os
import sys

_ENGINE_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_VENDOR = os.path.join(_ENGINE_ROOT, "vendor")
if _VENDOR not in sys.path:
    sys.path.insert(0, _VENDOR)
