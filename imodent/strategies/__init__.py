"""
Language Strategies for Indentation Fixing.

Each module here implements a LanguageStrategy for a specific format.
"""

from .json import JSONStrategy
from .jsonl import JSONLStrategy
from .python import PythonStrategy
from .yaml import YAMLStrategy

__all__ = ["JSONLStrategy", "JSONStrategy", "PythonStrategy", "YAMLStrategy"]
