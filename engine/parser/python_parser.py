import ast
import os
from pathlib import Path
from typing import List, Optional, Set, Dict, Any
from .base import BaseCodeParser, ParsedSymbol, SymbolType, ImportSymbol


class PythonCallVisitor(ast.NodeVisitor):
    """
    Sub-visitor to extract all function and method calls within a function/class body.
    """

    def __init__(self):
        self.calls: Set[str] = set()

    def visit_Call(self, node: ast.Call):
        func = node.func
        if isinstance(func, ast.Name):
            self.calls.add(func.id)
        elif isinstance(func, ast.Attribute):
            # E.g. self.do_something() or service.calculate()
            attr_name = self._get_attribute_name(func)
            if attr_name:
                self.calls.add(attr_name)
                # Also add just the method name for flexible matching
                self.calls.add(func.attr)
        self.generic_visit(node)

    def _get_attribute_name(self, node: ast.Attribute) -> Optional[str]:
        parts = []
        curr = node
        while isinstance(curr, ast.Attribute):
            parts.append(curr.attr)
            curr = curr.value
        if isinstance(curr, ast.Name):
            parts.append(curr.id)
            return ".".join(reversed(parts))
        return node.attr


class PythonASTParser(BaseCodeParser):
    """
    Production-ready AST parser for Python source files.
    Performs pure static analysis without loading or executing Python code.
    """

    @property
    def supported_language(self) -> str:
        return "python"

    @property
    def supported_extensions(self) -> List[str]:
        return [".py"]

    def is_test_file(self, file_path: str) -> bool:
        normalized = file_path.replace("\\", "/").lower()
        parts = normalized.split("/")
        filename = parts[-1]

        if filename.startswith("test_") or filename.endswith("_test.py"):
            return True
        if any(p in ("tests", "test", "testing", "testsuite") for p in parts):
            return True
        return False

    def _get_module_name(self, file_path: str) -> str:
        # Convert path/to/file.py to path.to.file
        p = Path(file_path)
        parts = list(p.parts)
        if parts and parts[-1].endswith(".py"):
            parts[-1] = parts[-1][:-3]
        if parts and parts[-1] == "__init__":
            parts.pop()
        return ".".join(parts) if parts else p.stem

    def parse_source(self, file_path: str, content: str) -> List[ParsedSymbol]:
        if not content:
            return []

        try:
            tree = ast.parse(content, filename=file_path)
        except (SyntaxError, ValueError, UnicodeDecodeError):
            # File has invalid syntax or encoding; return empty list gracefully
            return []

        symbols: List[ParsedSymbol] = []
        module_name = self._get_module_name(file_path)
        file_is_test = self.is_test_file(file_path)

        # 1. Module-level imports and symbol
        module_imports: List[ImportSymbol] = []
        for node in tree.body:
            if isinstance(node, ast.Import):
                for alias in node.names:
                    module_imports.append(
                        ImportSymbol(
                            module=alias.name,
                            imported_name=None,
                            alias=alias.asname,
                            is_from_import=False,
                            line_number=node.lineno,
                        )
                    )
            elif isinstance(node, ast.ImportFrom):
                mod = node.module or ""
                for alias in node.names:
                    module_imports.append(
                        ImportSymbol(
                            module=mod,
                            imported_name=alias.name,
                            alias=alias.asname,
                            is_from_import=True,
                            line_number=node.lineno,
                        )
                    )

        # Create module symbol
        total_lines = len(content.splitlines())
        symbols.append(
            ParsedSymbol(
                name=Path(file_path).stem,
                qualified_name=module_name,
                symbol_type=SymbolType.MODULE,
                file_path=file_path,
                start_line=1,
                end_line=total_lines or 1,
                docstring=ast.get_docstring(tree) or "",
                imports=module_imports,
                is_test=file_is_test,
            )
        )

        # 2. Extract classes and functions
        for node in tree.body:
            if isinstance(node, ast.ClassDef):
                self._parse_class(node, file_path, module_name, file_is_test, symbols)
            elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                self._parse_function(node, file_path, module_name, None, file_is_test, symbols)

        return symbols

    def _parse_class(
        self,
        node: ast.ClassDef,
        file_path: str,
        module_name: str,
        file_is_test: bool,
        symbols: List[ParsedSymbol],
    ):
        class_name = node.name
        qualified_name = f"{module_name}.{class_name}" if module_name else class_name
        is_test_class = file_is_test or class_name.startswith("Test") or class_name.endswith("Test")

        # Base classes
        base_classes = []
        for base in node.bases:
            if isinstance(base, ast.Name):
                base_classes.append(base.id)
            elif isinstance(base, ast.Attribute):
                base_classes.append(base.attr)

        decorators = [self._decorator_to_str(d) for d in node.decorator_list]
        docstring = ast.get_docstring(node) or ""

        end_line = getattr(node, "end_lineno", node.lineno)
        class_symbol = ParsedSymbol(
            name=class_name,
            qualified_name=qualified_name,
            symbol_type=SymbolType.CLASS,
            file_path=file_path,
            start_line=node.lineno,
            end_line=end_line,
            docstring=docstring,
            base_classes=base_classes,
            decorators=decorators,
            is_test=is_test_class,
        )
        symbols.append(class_symbol)

        # Methods inside class
        for item in node.body:
            if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
                self._parse_function(
                    item,
                    file_path,
                    module_name,
                    class_symbol,
                    is_test_class,
                    symbols,
                )

    def _parse_function(
        self,
        node: ast.FunctionDef | ast.AsyncFunctionDef,
        file_path: str,
        module_name: str,
        parent_class: Optional[ParsedSymbol],
        file_is_test: bool,
        symbols: List[ParsedSymbol],
    ):
        func_name = node.name
        if parent_class:
            qualified_name = f"{parent_class.qualified_name}.{func_name}"
            symbol_type = SymbolType.METHOD
            parent_name = parent_class.name
        else:
            qualified_name = f"{module_name}.{func_name}" if module_name else func_name
            symbol_type = SymbolType.FUNCTION
            parent_name = None

        is_test = (
            file_is_test
            or (parent_class and parent_class.is_test)
            or func_name.startswith("test_")
            or func_name.endswith("_test")
        )

        parameters = [arg.arg for arg in node.args.args]
        decorators = [self._decorator_to_str(d) for d in node.decorator_list]
        docstring = ast.get_docstring(node) or ""
        end_line = getattr(node, "end_lineno", node.lineno)

        # Extract internal calls
        call_visitor = PythonCallVisitor()
        for stmt in node.body:
            call_visitor.visit(stmt)

        symbol = ParsedSymbol(
            name=func_name,
            qualified_name=qualified_name,
            symbol_type=symbol_type,
            file_path=file_path,
            start_line=node.lineno,
            end_line=end_line,
            docstring=docstring,
            parameters=parameters,
            parent_name=parent_name,
            decorators=decorators,
            is_test=is_test,
            calls=call_visitor.calls,
        )
        symbols.append(symbol)

    def _decorator_to_str(self, node: ast.AST) -> str:
        if isinstance(node, ast.Name):
            return node.id
        elif isinstance(node, ast.Attribute):
            return f"{self._decorator_to_str(node.value)}.{node.attr}"
        elif isinstance(node, ast.Call):
            return self._decorator_to_str(node.func)
        return "decorator"
