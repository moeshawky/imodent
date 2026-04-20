"""
Language Strategies for Indentation Fixing.

Each module here implements a LanguageStrategy for a specific format.
"""

from .python import PythonStrategy
from .json import JSONStrategy
from .jsonl import JSONLStrategy
from .yaml import YAMLStrategy

__all__ = ["PythonStrategy", "JSONStrategy", "JSONLStrategy", "YAMLStrategy"]
