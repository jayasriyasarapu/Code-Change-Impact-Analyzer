from dataclasses import dataclass, field
from enum import Enum
from typing import List, Dict, Any


class RiskLevel(str, Enum):
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"
    CRITICAL = "CRITICAL"


@dataclass
class RiskAssessment:
    level: RiskLevel
    score: float
    rationales: List[str] = field(default_factory=list)
    breakdown: Dict[str, Any] = field(default_factory=dict)


class RiskCalculator:
    """
    Transparent, explainable risk scoring engine based on explicit architectural rules:
    1. Direct Dependent Fan-out (Max 30 pts)
    2. Transitive Depth & Total Volume (Max 25 pts)
    3. Test Coverage & Gap Detection (Max 25 pts)
    4. Change Nature / Breaking Potential (Max 20 pts)
    """

    @classmethod
    def evaluate(
        cls,
        total_changed: int,
        direct_dependents_count: int,
        transitive_dependents_count: int,
        max_transitive_depth: int,
        affected_tests_count: int,
        has_deletions: bool,
        tests_updated_in_diff: bool,
        only_test_changes: bool,
    ) -> RiskAssessment:
        if total_changed == 0:
            return RiskAssessment(
                level=RiskLevel.LOW,
                score=0.0,
                rationales=["No code components were modified in this change."],
                breakdown={"fan_out": 0, "volume": 0, "test_coverage": 0, "nature": 0},
            )

        if only_test_changes:
            return RiskAssessment(
                level=RiskLevel.LOW,
                score=10.0,
                rationales=["Changes are isolated exclusively to test files."],
                breakdown={"fan_out": 0, "volume": 0, "test_coverage": 0, "nature": 10},
            )

        rationales: List[str] = []
        score = 0.0

        # Rule 1: Direct Fan-out
        fan_out_score = 0.0
        if direct_dependents_count == 0:
            fan_out_score = 0.0
            rationales.append("Changed components have 0 direct downstream dependents (isolated leaf components).")
        elif 1 <= direct_dependents_count <= 2:
            fan_out_score = 10.0
            rationales.append(f"Changed components have {direct_dependents_count} direct dependent(s).")
        elif 3 <= direct_dependents_count <= 5:
            fan_out_score = 20.0
            rationales.append(f"Moderate fan-out: {direct_dependents_count} direct dependents affected.")
        else:
            fan_out_score = 30.0
            rationales.append(f"High blast radius: {direct_dependents_count} components directly depend on these changes.")
        score += fan_out_score

        # Rule 2: Transitive Volume and Depth
        volume_score = 0.0
        if transitive_dependents_count == 0:
            volume_score = 0.0
        elif transitive_dependents_count <= 5:
            volume_score = 10.0
            rationales.append(f"Small transitive ripple: {transitive_dependents_count} indirect component(s) impacted (max depth {max_transitive_depth}).")
        elif transitive_dependents_count <= 20:
            volume_score = 18.0
            rationales.append(f"Substantial ripple effect: {transitive_dependents_count} indirect components impacted across depth {max_transitive_depth}.")
        else:
            volume_score = 25.0
            rationales.append(f"Widespread codebase impact: >20 ({transitive_dependents_count}) components impacted at depth {max_transitive_depth}.")
        score += volume_score

        # Rule 3: Test Coverage & Regression Safety
        test_score = 0.0
        if affected_tests_count == 0:
            test_score = 25.0
            rationales.append("No automated test suites detectably cover the changed or impacted components (uncovered regression risk).")
        elif not tests_updated_in_diff:
            test_score = 15.0
            rationales.append(f"{affected_tests_count} test(s) cover changed code, but no tests were added or updated in this diff.")
        else:
            test_score = 5.0
            rationales.append(f"High test verification: {affected_tests_count} affected test(s) identified and test files were actively updated in this change.")
        score += test_score

        # Rule 4: Nature of Change (Deletions / Breaking Changes)
        nature_score = 0.0
        if has_deletions:
            nature_score = 20.0
            rationales.append("Change includes deletion of existing component(s) which carries potential breaking contract risk.")
        else:
            nature_score = 10.0
            rationales.append("Changes are non-destructive (modifications and additions).")
        score += nature_score

        # Normalize score
        final_score = min(100.0, max(0.0, score))

        if final_score <= 20.0:
            level = RiskLevel.LOW
        elif final_score <= 50.0:
            level = RiskLevel.MEDIUM
        elif final_score <= 75.0:
            level = RiskLevel.HIGH
        else:
            level = RiskLevel.CRITICAL

        return RiskAssessment(
            level=level,
            score=final_score,
            rationales=rationales,
            breakdown={
                "fan_out": fan_out_score,
                "volume": volume_score,
                "test_coverage": test_score,
                "nature": nature_score,
            },
        )
