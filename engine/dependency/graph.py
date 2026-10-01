from dataclasses import dataclass, field
from typing import Dict, List, Set, Optional, Tuple, Any
from collections import deque
import networkx as nx
from engine.parser.base import ParsedSymbol, SymbolType


@dataclass
class DependencyEdge:
    source: str  # Dependent symbol (e.g. caller)
    target: str  # Dependency symbol (e.g. callee)
    dependency_type: str  # CALLS, INHERITS, IMPORTS, REFERENCES
    confidence: float = 1.0


class SymbolDependencyGraph:
    """
    In-memory directed dependency graph powered by NetworkX.
    Nodes represent symbols (modules, classes, functions, methods).
    Directed edges represent dependency relationships:
    Edge (A -> B) means symbol A depends on symbol B (A calls B, or A inherits B).
    In the reverse (impact) direction, a change in B directly affects A.
    """

    def __init__(self):
        # Graph where edge (dependent, dependency) means dependent depends on dependency
        self.graph = nx.DiGraph()
        # Symbol registry: qualified_name -> ParsedSymbol
        self.symbols: Dict[str, ParsedSymbol] = {}
        # Short-name index: short_name -> list of qualified_names (for call resolution)
        self.short_name_map: Dict[str, List[str]] = {}

    def add_symbol(self, symbol: ParsedSymbol):
        self.symbols[symbol.qualified_name] = symbol
        self.graph.add_node(
            symbol.qualified_name,
            name=symbol.name,
            type=symbol.symbol_type.value,
            file_path=symbol.file_path,
            start_line=symbol.start_line,
            end_line=symbol.end_line,
            is_test=symbol.is_test,
        )

        # Index short names
        if symbol.name not in self.short_name_map:
            self.short_name_map[symbol.name] = []
        if symbol.qualified_name not in self.short_name_map[symbol.name]:
            self.short_name_map[symbol.name].append(symbol.qualified_name)

    def add_dependency(
        self,
        dependent: str,
        dependency: str,
        dependency_type: str = "CALLS",
        confidence: float = 1.0,
    ):
        if dependent in self.graph and dependency in self.graph:
            self.graph.add_edge(
                dependent,
                dependency,
                dependency_type=dependency_type,
                confidence=confidence,
            )

    def build_from_symbols(self, all_symbols: List[ParsedSymbol]):
        """
        Builds graph nodes and resolves dependencies (imports, inheritance, and calls)
        across all parsed symbols.
        """
        # Step 1: Register all symbols
        for sym in all_symbols:
            self.add_symbol(sym)

        # Step 2: Build file import mappings: (file_path, imported_name/alias) -> target qualified name
        file_import_map: Dict[str, Dict[str, str]] = {}
        for sym in all_symbols:
            if sym.symbol_type == SymbolType.MODULE:
                file_import_map[sym.file_path] = {}
                for imp in sym.imports:
                    alias = imp.alias or imp.imported_name or imp.module
                    if imp.is_from_import and imp.imported_name:
                        target = f"{imp.module}.{imp.imported_name}"
                    else:
                        target = imp.module
                    file_import_map[sym.file_path][alias] = target

        # Step 3: Resolve inheritance and calls
        for sym in all_symbols:
            file_imports = file_import_map.get(sym.file_path, {})

            # 3a. Class Inheritance
            if sym.symbol_type == SymbolType.CLASS:
                for base in sym.base_classes:
                    target_qname = self._resolve_target(base, sym, file_imports)
                    if target_qname:
                        self.add_dependency(sym.qualified_name, target_qname, "INHERITS", 1.0)

            # 3b. Function/Method Calls
            if sym.symbol_type in (SymbolType.FUNCTION, SymbolType.METHOD):
                for call_name in sym.calls:
                    target_qname = self._resolve_target(call_name, sym, file_imports)
                    if target_qname and target_qname != sym.qualified_name:
                        self.add_dependency(sym.qualified_name, target_qname, "CALLS", 1.0)

    def _resolve_target(
        self,
        raw_name: str,
        source_sym: ParsedSymbol,
        file_imports: Dict[str, str],
    ) -> Optional[str]:
        """
        Resolves a call or inheritance string to a qualified symbol name.
        """
        # Exact match in file imports
        if raw_name in file_imports:
            imp_target = file_imports[raw_name]
            if imp_target in self.symbols:
                return imp_target
            # If target is a module, check if module has a symbol with raw_name
            candidate = f"{imp_target}.{raw_name}"
            if candidate in self.symbols:
                return candidate

        # Attribute call resolution: e.g. service.process -> check if 'service' is imported
        if "." in raw_name:
            prefix, attr = raw_name.split(".", 1)
            if prefix in file_imports:
                target_base = file_imports[prefix]
                full_cand = f"{target_base}.{attr}"
                if full_cand in self.symbols:
                    return full_cand

        # Same file / module check
        module_qname = source_sym.qualified_name.rsplit(".", 1)[0] if "." in source_sym.qualified_name else ""
        if module_qname:
            local_cand = f"{module_qname}.{raw_name}"
            if local_cand in self.symbols:
                return local_cand

        # Fallback to unique short name match across project
        matches = self.short_name_map.get(raw_name, [])
        if len(matches) == 1:
            return matches[0]

        return None

    def get_direct_dependents(self, symbol_qname: str) -> List[Dict[str, Any]]:
        """
        Returns all symbols that directly depend on symbol_qname.
        (Nodes that have an edge pointing to symbol_qname).
        """
        if symbol_qname not in self.graph:
            return []

        direct = []
        for caller in self.graph.predecessors(symbol_qname):
            edge_data = self.graph.get_edge_data(caller, symbol_qname, default={})
            sym = self.symbols.get(caller)
            direct.append({
                "qualified_name": caller,
                "name": sym.name if sym else caller,
                "file_path": sym.file_path if sym else "",
                "symbol_type": sym.symbol_type.value if sym else "UNKNOWN",
                "dependency_type": edge_data.get("dependency_type", "CALLS"),
                "is_test": sym.is_test if sym else False,
            })
        return direct

    def get_transitive_dependents(
        self,
        changed_qnames: List[str],
        max_depth: int = 15,
    ) -> Dict[str, Dict[str, Any]]:
        """
        Performs BFS in the reverse direction (impact propagation) to find all
        symbols directly and transitively impacted by changes in `changed_qnames`.
        Avoids infinite cycles and tracks exact propagation paths.
        """
        impacted: Dict[str, Dict[str, Any]] = {}
        queue = deque()

        # Initialize with changed nodes
        for qname in changed_qnames:
            if qname in self.graph:
                queue.append((qname, 0, [qname]))

        visited = set()

        while queue:
            curr_qname, depth, path = queue.popleft()

            if depth >= max_depth:
                continue

            # Upstream callers/inheritors depend on curr_qname
            for dependent in self.graph.predecessors(curr_qname):
                edge_data = self.graph.get_edge_data(dependent, curr_qname, default={})
                new_path = path + [dependent]
                state_key = (dependent, depth + 1)

                if state_key in visited:
                    continue
                visited.add(state_key)

                sym = self.symbols.get(dependent)
                is_test = sym.is_test if sym else False

                # Register impacted symbol (or update if found via shorter depth)
                if dependent not in impacted or depth + 1 < impacted[dependent]["depth"]:
                    impacted[dependent] = {
                        "qualified_name": dependent,
                        "name": sym.name if sym else dependent,
                        "file_path": sym.file_path if sym else "",
                        "symbol_type": sym.symbol_type.value if sym else "UNKNOWN",
                        "depth": depth + 1,
                        "impact_type": "DIRECT" if (depth + 1) == 1 else "TRANSITIVE",
                        "dependency_path": new_path,
                        "is_test": is_test,
                        "reason": f"{'Directly' if (depth + 1) == 1 else 'Transitively'} depends on {curr_qname} via {edge_data.get('dependency_type', 'CALLS')}",
                    }

                queue.append((dependent, depth + 1, new_path))

        return impacted

    def to_dict(self) -> Dict[str, Any]:
        """
        Serializes nodes and edges to JSON-serializable dictionary.
        """
        nodes = []
        for node, attrs in self.graph.nodes(data=True):
            nodes.append({
                "id": node,
                "label": attrs.get("name", node),
                "type": attrs.get("type", "FUNCTION"),
                "file_path": attrs.get("file_path", ""),
                "is_test": attrs.get("is_test", False),
            })

        edges = []
        for u, v, attrs in self.graph.edges(data=True):
            edges.append({
                "source": u,
                "target": v,
                "type": attrs.get("dependency_type", "CALLS"),
                "confidence": attrs.get("confidence", 1.0),
            })

        return {"nodes": nodes, "edges": edges}
