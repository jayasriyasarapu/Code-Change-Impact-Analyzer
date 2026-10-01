from dataclasses import dataclass, field
from typing import List, Dict, Any, Optional, Set
from pathlib import Path
from engine.diff_parser import FileDiff
from engine.dependency.graph import SymbolDependencyGraph
from engine.parser.base import ParsedSymbol, SymbolType
from .risk_calculator import RiskCalculator, RiskAssessment, RiskLevel


@dataclass
class ChangedComponentData:
    file_path: str
    name: str
    qualified_name: str
    change_type: str  # ADDED, MODIFIED, DELETED
    lines_added: int
    lines_deleted: int
    start_line: int
    end_line: int
    diff_snippet: str = ""
    component_type: str = "FUNCTION"


@dataclass
class ImpactedComponentData:
    file_path: str
    name: str
    qualified_name: str
    impact_type: str  # DIRECT, TRANSITIVE
    depth: int
    dependency_path: List[str]
    is_test: bool
    reason: str
    risk_level: str
    component_type: str = "FUNCTION"


@dataclass
class ImpactReport:
    total_changed_files: int
    total_changed_components: int
    total_impacted_components: int
    total_affected_tests: int
    risk_assessment: RiskAssessment
    changed_components: List[ChangedComponentData] = field(default_factory=list)
    impacted_components: List[ImpactedComponentData] = field(default_factory=list)
    affected_tests: List[ImpactedComponentData] = field(default_factory=list)
    summary: Dict[str, Any] = field(default_factory=dict)


class ImpactAnalyzer:
    """
    Orchestrates AST diff correlation, dependency propagation,
    test identification, and explainable risk computation.
    """

    def analyze(
        self,
        file_diffs: List[FileDiff],
        graph: SymbolDependencyGraph,
        new_file_symbols: Optional[List[ParsedSymbol]] = None,
    ) -> ImpactReport:
        changed_components: List[ChangedComponentData] = []
        changed_qnames: Set[str] = set()
        has_deletions = False
        tests_updated_in_diff = False
        all_changed_files_are_tests = True

        # Pre-index new file symbols if any (e.g. freshly added files)
        if new_file_symbols:
            for sym in new_file_symbols:
                graph.add_symbol(sym)

        # 1. Map diff line numbers to AST symbols
        for fdiff in file_diffs:
            file_path = fdiff.display_path
            is_test_file = (
                "test_" in file_path.lower()
                or "_test.py" in file_path.lower()
                or "tests/" in file_path.lower()
                or "tests\\" in file_path.lower()
            )
            if is_test_file:
                tests_updated_in_diff = True
            else:
                all_changed_files_are_tests = False

            if fdiff.change_type == "DELETED":
                has_deletions = True

            # Match lines to symbols belonging to this file
            file_symbols = [
                s for s in graph.symbols.values()
                if self._paths_match(s.file_path, file_path)
            ]

            touched_symbols_in_file: Set[str] = set()

            # Inspect modified lines
            all_touched_lines = set(fdiff.added_line_numbers + fdiff.deleted_line_numbers)

            for sym in file_symbols:
                # Check line overlap
                sym_range = set(range(sym.start_line, sym.end_line + 1))
                if all_touched_lines.intersection(sym_range) or fdiff.change_type in ("ADDED", "DELETED"):
                    touched_symbols_in_file.add(sym.qualified_name)
                    change_type = fdiff.change_type
                    if change_type == "MODIFIED" and fdiff.deleted_line_numbers and not fdiff.added_line_numbers:
                        # Deletions inside symbol
                        pass

                    changed_components.append(
                        ChangedComponentData(
                            file_path=file_path,
                            name=sym.name,
                            qualified_name=sym.qualified_name,
                            change_type=change_type,
                            lines_added=len(fdiff.added_line_numbers),
                            lines_deleted=len(fdiff.deleted_line_numbers),
                            start_line=sym.start_line,
                            end_line=sym.end_line,
                            diff_snippet=fdiff.raw_diff[:500],
                            component_type=sym.symbol_type.value,
                        )
                    )
                    changed_qnames.add(sym.qualified_name)

            # If no fine-grained symbol was touched (e.g. global comments or config), add file-level component
            if not touched_symbols_in_file and (fdiff.added_line_numbers or fdiff.deleted_line_numbers):
                changed_components.append(
                    ChangedComponentData(
                        file_path=file_path,
                        name=Path(file_path).name,
                        qualified_name=file_path,
                        change_type=fdiff.change_type,
                        lines_added=len(fdiff.added_line_numbers),
                        lines_deleted=len(fdiff.deleted_line_numbers),
                        start_line=1,
                        end_line=1,
                        diff_snippet=fdiff.raw_diff[:500],
                        component_type="FILE",
                    )
                )

        # 2. Traverse dependency graph for downstream impact
        raw_impacted = graph.get_transitive_dependents(list(changed_qnames))

        impacted_components: List[ImpactedComponentData] = []
        affected_tests: List[ImpactedComponentData] = []
        direct_count = 0
        transitive_count = 0
        max_depth = 0

        for qname, data in raw_impacted.items():
            depth = data["depth"]
            max_depth = max(max_depth, depth)
            if depth == 1:
                direct_count += 1
            else:
                transitive_count += 1

            # Determine risk level per component
            if data["is_test"]:
                comp_risk = "LOW"
            elif depth == 1:
                comp_risk = "HIGH"
            else:
                comp_risk = "MEDIUM"

            impacted_item = ImpactedComponentData(
                file_path=data["file_path"],
                name=data["name"],
                qualified_name=qname,
                impact_type=data["impact_type"],
                depth=depth,
                dependency_path=data["dependency_path"],
                is_test=data["is_test"],
                reason=data["reason"],
                risk_level=comp_risk,
                component_type=data.get("symbol_type", "FUNCTION"),
            )
            impacted_components.append(impacted_item)

            if data["is_test"]:
                affected_tests.append(impacted_item)

        # 3. Calculate Overall Risk Score
        risk_assessment = RiskCalculator.evaluate(
            total_changed=len(changed_components),
            direct_dependents_count=direct_count,
            transitive_dependents_count=transitive_count,
            max_transitive_depth=max_depth,
            affected_tests_count=len(affected_tests),
            has_deletions=has_deletions,
            tests_updated_in_diff=tests_updated_in_diff,
            only_test_changes=all_changed_files_are_tests and len(changed_components) > 0,
        )

        # 4. Generate Summary Dictionary
        summary = {
            "total_files_analyzed": len(file_diffs),
            "direct_dependents_count": direct_count,
            "transitive_dependents_count": transitive_count,
            "max_propagation_depth": max_depth,
            "affected_tests_count": len(affected_tests),
            "risk_level": risk_assessment.level.value,
            "risk_score": risk_assessment.score,
            "risk_rationales": risk_assessment.rationales,
        }

        return ImpactReport(
            total_changed_files=len(file_diffs),
            total_changed_components=len(changed_components),
            total_impacted_components=len(impacted_components),
            total_affected_tests=len(affected_tests),
            risk_assessment=risk_assessment,
            changed_components=changed_components,
            impacted_components=impacted_components,
            affected_tests=affected_tests,
            summary=summary,
        )

    def _paths_match(self, path1: str, path2: str) -> bool:
        p1 = path1.replace("\\", "/").strip("/").lower()
        p2 = path2.replace("\\", "/").strip("/").lower()
        return p1 == p2 or p1.endswith("/" + p2) or p2.endswith("/" + p1)
