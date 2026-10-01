from pathlib import Path
from typing import Dict, Optional, Type
from .base import BaseCodeParser
from .python_parser import PythonASTParser


class ParserRegistry:
    """
    Central registry for language parsers supporting extensibility.
    """

    _parsers_by_ext: Dict[str, BaseCodeParser] = {}
    _parsers_by_lang: Dict[str, BaseCodeParser] = {}

    @classmethod
    def register(cls, parser_instance: BaseCodeParser):
        cls._parsers_by_lang[parser_instance.supported_language.lower()] = parser_instance
        for ext in parser_instance.supported_extensions:
            cls._parsers_by_ext[ext.lower()] = parser_instance

    @classmethod
    def get_parser_for_file(cls, file_path: str) -> Optional[BaseCodeParser]:
        ext = Path(file_path).suffix.lower()
        return cls._parsers_by_ext.get(ext)

    @classmethod
    def get_parser_for_language(cls, language: str) -> Optional[BaseCodeParser]:
        return cls._parsers_by_lang.get(language.lower())


# Register default Python parser
ParserRegistry.register(PythonASTParser())
