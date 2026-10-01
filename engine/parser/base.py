from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum
from typing import List, Optional, Set, Dict, Any


class SymbolType(str, Enum):
    MODULE = "MODULE"
    CLASS = "CLASS"
    FUNCTION = "FUNCTION"
    METHOD = "METHOD"
    VARIABLE = "VARIABLE"


@dataclass
class ImportSymbol:
    module: str
    imported_name: Optional[str] = None
    alias: Optional[str] = None
    is_from_import: bool = False
    line_number: int = 1


@dataclass
class ParsedSymbol:
    name: str
    qualified_name: str
    symbol_type: SymbolType
    file_path: str
    start_line: int
    end_line: int
    docstring: str = ""
    parameters: List[str] = field(default_factory=list)
    return_annotation: Optional[str] = None
    parent_name: Optional[str] = None
    decorators: List[str] = field(default_factory=list)
    is_test: bool = False
    calls: Set[str] = field(default_factory=set)  # Function/method names called inside this symbol
    imports: List[ImportSymbol] = field(default_factory=list)
    base_classes: List[str] = field(default_factory=list)  # For classes
    extra_metadata: Dict[str, Any] = field(default_factory=dict)

    def contains_line(self, line_number: int) -> bool:
        return self.start_line <= line_number <= self.end_line


class BaseCodeParser(ABC):
    """
    Abstract interface for language-specific static AST analysis.
    Implementations must not execute untrusted source code.
    """

    @property
    @abstractmethod
    def supported_language(self) -> str:
        """Name of the supported language (e.g. 'python', 'javascript')."""
        pass

    @property
    @abstractmethod
    def supported_extensions(self) -> List[str]:
        """List of file extensions handled by this parser (e.g. ['.py'])."""
        pass

    @abstractmethod
    def parse_source(self, file_path: str, content: str) -> List[ParsedSymbol]:
        """
        Parses source code text and returns all extracted symbols with metadata.
        """
        pass

    @abstractmethod
    def is_test_file(self, file_path: str) -> bool:
        """Determines if the given file path represents a test suite."""
        pass
