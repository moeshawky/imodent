"""
Registry for Language Strategies and Processors.

Provides centralized registration and lookup for all available strategies and processors.
"""
from typing import Dict, List, Type, Optional
from .interfaces import LanguageStrategy, Processor


class StrategyRegistry:
    """
    Registry for LanguageStrategy implementations.
    
    Automatically discovers and manages all registered language strategies.
    """
    
    _strategies: Dict[str, Type[LanguageStrategy]] = {}
    _by_extension: Dict[str, Type[LanguageStrategy]] = {}
    
    @classmethod
    def register(cls, strategy_class: Type[LanguageStrategy]) -> Type[LanguageStrategy]:
        """
        Register a language strategy.
        
        Usage:
            @StrategyRegistry.register
            class PythonStrategy(LanguageStrategy):
                ...
        """
        if not issubclass(strategy_class, LanguageStrategy):
            raise TypeError(f"{strategy_class.__name__} must be a subclass of LanguageStrategy")
        
        instance = strategy_class()
        cls._strategies[instance.name] = strategy_class
        
        for ext in instance.extensions:
            cls._by_extension[ext.lower()] = strategy_class
        
        return strategy_class
    
    @classmethod
    def get(cls, name: str) -> Optional[Type[LanguageStrategy]]:
        """Get a strategy by name."""
        return cls._strategies.get(name)
    
    @classmethod
    def get_by_extension(cls, extension: str) -> Optional[Type[LanguageStrategy]]:
        """Get a strategy by file extension."""
        return cls._by_extension.get(extension.lower())
    
    @classmethod
    def detect(cls, content: str) -> Optional[Type[LanguageStrategy]]:
        """
        Detect the appropriate strategy for the given content.
        
        Returns the first strategy that claims the content.
        """
        for strategy_class in cls._strategies.values():
            instance = strategy_class()
            if instance.detect(content):
                return strategy_class
        return None
    
    @classmethod
    def all(cls) -> List[Type[LanguageStrategy]]:
        """Get all registered strategies."""
        return list(cls._strategies.values())
    
    @classmethod
    def clear(cls):
        """Clear all registered strategies (useful for testing)."""
        cls._strategies.clear()
        cls._by_extension.clear()


class ProcessorRegistry:
    """
    Registry for Processor implementations.
    """
    
    _processors: Dict[str, Type[Processor]] = {}
    
    @classmethod
    def register(cls, processor_class: Type[Processor]) -> Type[Processor]:
        """Register a processor."""
        if not issubclass(processor_class, Processor):
            raise TypeError(f"{processor_class.__name__} must be a subclass of Processor")
        
        instance = processor_class()
        cls._processors[instance.name] = processor_class
        return processor_class
    
    @classmethod
    def get(cls, name: str) -> Optional[Type[Processor]]:
        """Get a processor by name."""
        return cls._processors.get(name)
    
    @classmethod
    def all(cls) -> List[Type[Processor]]:
        """Get all registered processors."""
        return list(cls._processors.values())
    
    @classmethod
    def clear(cls):
        """Clear all registered processors."""
        cls._processors.clear()
