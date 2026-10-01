from __future__ import annotations

import ast
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple


@dataclass
class TestCaseResult:
    name: str
    status: str  # "PASSED", "FAILED", "ERROR"
    duration: float = 0.0
    message: str = ""
    error_type: str = ""
    line_number: Optional[int] = None
    expected: Optional[str] = None
    actual: Optional[str] = None
    likely_cause: str = ""
    suggested_fix: str = ""


@dataclass
class CodeAnalysisReport:
    valid_syntax: bool
    syntax_error: Optional[Dict[str, Any]] = None
    overview: str = ""
    functions: List[Dict[str, Any]] = field(default_factory=list)
    classes: List[Dict[str, Any]] = field(default_factory=list)
    execution_flow: List[Dict[str, Any]] = field(default_factory=list)
    risks: List[Dict[str, Any]] = field(default_factory=list)
    risk_level: str = "LOW"
    risk_score: float = 10.0
    generated_tests: str = ""
    test_run: Optional[Dict[str, Any]] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "valid_syntax": self.valid_syntax,
            "syntax_error": self.syntax_error,
            "overview": self.overview,
            "functions": self.functions,
            "classes": self.classes,
            "execution_flow": self.execution_flow,
            "risks": self.risks,
            "risk_level": self.risk_level,
            "risk_score": self.risk_score,
            "generated_tests": self.generated_tests,
            "test_run": self.test_run,
        }


class CodeAnalyzer:
    """
    Analyzes raw Python code to extract symbols, generate plain-English explanations,
    detect edge-case risks, automatically generate test suites, and execute tests.
    """

    def __init__(self, timeout_seconds: int = 15):
        self.timeout_seconds = timeout_seconds

    def analyze(self, source_code: str, custom_tests: Optional[str] = None) -> Dict[str, Any]:
        source_code = source_code.strip()
        if not source_code:
            return CodeAnalysisReport(
                valid_syntax=False,
                overview="No code was provided to analyze.",
                risks=[{"severity": "HIGH", "title": "Empty Code", "description": "Please provide Python code to analyze."}],
                risk_level="HIGH",
                risk_score=75.0,
            ).to_dict()

        # 1. Syntax & Compilation Verification
        try:
            compile(source_code, "source.py", "exec")
            tree = ast.parse(source_code, filename="source.py")
        except SyntaxError as err:
            advice = ""
            if "return" in str(err.msg).lower() and "outside" in str(err.msg).lower():
                advice = " In Python, a `return` statement cannot exist on its own outside a function. Wrap it inside a function definition (for example: `def divide(a, b): return a / b`)."
            elif "unexpected indent" in str(err.msg).lower():
                advice = " Check indentation alignment. Python uses consistent 4-space indentation for code blocks."
            elif "invalid syntax" in str(err.msg).lower():
                advice = " Check for missing colons `:` after `def`, `if`, `for`, or unclosed parentheses `()`."

            return CodeAnalysisReport(
                valid_syntax=False,
                syntax_error={
                    "line": err.lineno or 1,
                    "offset": err.offset or 1,
                    "text": (err.text or "").strip(),
                    "message": str(err.msg),
                    "advice": advice,
                },
                overview=f"Syntax compilation error on line {err.lineno or 1}: {err.msg}.{advice}",
                risks=[{
                    "severity": "CRITICAL",
                    "title": f"Syntax Error: {err.msg} (Line {err.lineno or 1})",
                    "description": f"Python cannot execute this code because of a syntax error on line {err.lineno or 1}: `{(err.text or '').strip()}`.{advice}",
                }],
                risk_level="CRITICAL",
                risk_score=95.0,
            ).to_dict()

        # 2. Extract AST metadata & symbols
        functions_meta = []
        classes_meta = []
        risks = []
        execution_flow = []

        for node in tree.body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                fn_info = self._analyze_function(node, source_code)
                functions_meta.append(fn_info)
                execution_flow.append({
                    "type": "function",
                    "name": fn_info["name"],
                    "line": f"{node.lineno}-{getattr(node, 'end_lineno', node.lineno)}",
                    "explanation": fn_info["explanation"],
                    "parameters": fn_info["parameters"],
                    "returns": fn_info["returns"],
                })
            elif isinstance(node, ast.ClassDef):
                cls_info = self._analyze_class(node, source_code)
                classes_meta.append(cls_info)
                execution_flow.append({
                    "type": "class",
                    "name": cls_info["name"],
                    "line": f"{node.lineno}-{getattr(node, 'end_lineno', node.lineno)}",
                    "explanation": cls_info["explanation"],
                    "methods": [m["name"] for m in cls_info["methods"]],
                })
            elif isinstance(node, ast.Assign):
                targets = [self._ast_to_str(t) for t in node.targets]
                val_str = self._ast_to_str(node.value)
                execution_flow.append({
                    "type": "assignment",
                    "name": ", ".join(targets),
                    "line": str(node.lineno),
                    "explanation": f"Initializes global variable `{', '.join(targets)}` with value `{val_str}`.",
                })

        # Check for standalone top-level expressions or returns
        for node in tree.body:
            if isinstance(node, ast.Return):
                risks.append({
                    "severity": "CRITICAL",
                    "title": "Return Statement Outside Function",
                    "description": f"Line {node.lineno} has `return {self._ast_to_str(node.value)}` outside any function body. In Python, `return` can only be used inside a function (`def`). Wrap this inside a function or replace it with a print statement.",
                })

        # Deep Risk Detection across AST
        risks.extend(self._detect_risks(tree, source_code))

        # Overall Overview Generation
        fn_count = len(functions_meta)
        cls_count = len(classes_meta)
        total_lines = len(source_code.splitlines())
        
        overview_parts = [f"This Python module contains {total_lines} line(s) of code"]
        if fn_count > 0:
            overview_parts.append(f"defining {fn_count} function(s): {', '.join(f'`{f['name']}`' for f in functions_meta)}")
        if cls_count > 0:
            overview_parts.append(f"and {cls_count} class(es): {', '.join(f'`{c['name']}`' for c in classes_meta)}")
        if fn_count == 0 and cls_count == 0:
            overview_parts.append("consisting of top-level procedural statements")
        overview = ". ".join(overview_parts) + "."

        # Compute Risk Level
        risk_score = 10.0
        has_critical = any(r["severity"] == "CRITICAL" for r in risks)
        has_high = any(r["severity"] == "HIGH" for r in risks)
        has_medium = any(r["severity"] == "MEDIUM" for r in risks)

        if has_critical:
            risk_level = "CRITICAL"
            risk_score = 90.0
        elif has_high:
            risk_level = "HIGH"
            risk_score = 65.0
        elif has_medium:
            risk_level = "MEDIUM"
            risk_score = 35.0
        else:
            risk_level = "LOW"
            risk_score = 10.0

        # 3. Generate Smart Test Cases
        generated_tests = self._generate_test_cases(functions_meta, classes_meta, source_code)

        # 4. Execute Tests
        tests_to_run = (custom_tests.strip() if custom_tests and custom_tests.strip() else generated_tests)
        test_run_result = self._execute_test_suite(source_code, tests_to_run)

        # If tests failed, adjust risk
        if test_run_result and test_run_result["failed"] > 0:
            if risk_level != "CRITICAL":
                risk_level = "HIGH"
            risk_score = max(risk_score, 75.0)

        return CodeAnalysisReport(
            valid_syntax=True,
            overview=overview,
            functions=functions_meta,
            classes=classes_meta,
            execution_flow=execution_flow,
            risks=risks,
            risk_level=risk_level,
            risk_score=risk_score,
            generated_tests=generated_tests,
            test_run=test_run_result,
        ).to_dict()

    def _analyze_function(self, node: ast.FunctionDef | ast.AsyncFunctionDef, source: str) -> Dict[str, Any]:
        name = node.name
        params = [arg.arg for arg in node.args.args]
        
        # Check return expressions
        returns = []
        for child in ast.walk(node):
            if isinstance(child, ast.Return) and child.value:
                returns.append(self._ast_to_str(child.value))

        # Check operations inside function
        operations = []
        calls = []
        has_div = False
        for child in ast.walk(node):
            if isinstance(child, ast.BinOp):
                op_name = type(child.op).__name__
                if isinstance(child.op, ast.Add):
                    operations.append("addition (`+`)")
                elif isinstance(child.op, ast.Sub):
                    operations.append("subtraction (`-`)")
                elif isinstance(child.op, ast.Mult):
                    operations.append("multiplication (`*`)")
                elif isinstance(child.op, ast.Div):
                    has_div = True
                    operations.append("floating-point division (`/`)")
                elif isinstance(child.op, ast.FloorDiv):
                    has_div = True
                    operations.append("integer floor division (`//`)")
                elif isinstance(child.op, ast.Mod):
                    has_div = True
                    operations.append("modulo division (`%`)")
                elif isinstance(child.op, ast.Pow):
                    operations.append("exponentiation (`**`)")
            elif isinstance(child, ast.Call):
                call_name = self._ast_to_str(child.func)
                calls.append(call_name)

        # Synthesize explanation
        explanation_parts = []
        if params:
            explanation_parts.append(f"Accepts {len(params)} argument(s) (`{', '.join(params)}`).")
        else:
            explanation_parts.append("Accepts no arguments.")

        if operations:
            unique_ops = list(dict.fromkeys(operations))
            explanation_parts.append(f"Performs {', '.join(unique_ops)}.")

        if returns:
            explanation_parts.append(f"Returns `{', '.join(returns)}`.")
        else:
            explanation_parts.append("No explicit return statement (returns `None`).")

        if has_div:
            explanation_parts.append("Note: Involves division; requires handling when the divisor is 0.")

        explanation = " ".join(explanation_parts)

        return {
            "name": name,
            "line_start": node.lineno,
            "line_end": getattr(node, "end_lineno", node.lineno),
            "parameters": params,
            "returns": returns,
            "operations": list(dict.fromkeys(operations)),
            "calls": list(dict.fromkeys(calls)),
            "explanation": explanation,
            "docstring": ast.get_docstring(node) or "",
        }

    def _analyze_class(self, node: ast.ClassDef, source: str) -> Dict[str, Any]:
        bases = [self._ast_to_str(b) for b in node.bases]
        methods = []
        for child in node.body:
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                methods.append(self._analyze_function(child, source))

        explanation = f"Class `{node.name}`"
        if bases:
            explanation += f" inheriting from `{', '.join(bases)}`"
        explanation += f" with {len(methods)} method(s): {', '.join(f'`{m['name']}`' for m in methods)}."

        return {
            "name": node.name,
            "line_start": node.lineno,
            "line_end": getattr(node, "end_lineno", node.lineno),
            "bases": bases,
            "methods": methods,
            "explanation": explanation,
            "docstring": ast.get_docstring(node) or "",
        }

    def _detect_risks(self, tree: ast.AST, source: str) -> List[Dict[str, Any]]:
        risks = []
        lines = source.splitlines()

        for node in ast.walk(tree):
            # Check division by zero risk
            if isinstance(node, ast.BinOp) and isinstance(node.op, (ast.Div, ast.FloorDiv, ast.Mod)):
                divisor_str = self._ast_to_str(node.right)
                line_no = node.lineno
                snippet = lines[line_no - 1] if 0 < line_no <= len(lines) else ""
                
                if isinstance(node.right, ast.Constant) and node.right.value == 0:
                    risks.append({
                        "severity": "CRITICAL",
                        "title": f"Explicit Division by Zero (Line {line_no})",
                        "description": f"Code directly divides by zero: `{snippet.strip()}`. This will raise a `ZeroDivisionError` immediately.",
                    })
                else:
                    risks.append({
                        "severity": "HIGH",
                        "title": f"Potential ZeroDivisionError (Line {line_no})",
                        "description": f"Division by `{divisor_str}` in `{snippet.strip()}` has no zero-check. If `{divisor_str} == 0`, Python will crash with `ZeroDivisionError`. Add a guard condition `if {divisor_str} == 0:` or `try/except ZeroDivisionError:`.",
                    })

            # Check bare except
            elif isinstance(node, ast.ExceptHandler) and node.type is None:
                risks.append({
                    "severity": "MEDIUM",
                    "title": f"Bare 'except:' Clause (Line {node.lineno})",
                    "description": "Catching all exceptions with a bare `except:` masks unexpected bugs, SystemExit, and KeyboardInterrupt. Use `except Exception as e:` instead.",
                })

            # Check while True without break
            elif isinstance(node, ast.While) and isinstance(node.test, ast.Constant) and node.test.value is True:
                has_break = any(isinstance(c, (ast.Break, ast.Return)) for c in ast.walk(node))
                if not has_break:
                    risks.append({
                        "severity": "CRITICAL",
                        "title": f"Infinite Loop Detected (Line {node.lineno})",
                        "description": "A `while True:` loop has no `break` or `return` statement, which will cause an infinite loop and hang the process.",
                    })

        return risks

    def _generate_test_cases(self, functions: List[Dict[str, Any]], classes: List[Dict[str, Any]], source: str) -> str:
        """
        Generates sensible, robust pytest assertions covering happy paths and edge cases.
        """
        lines = [
            "import pytest",
            "from main import *",
            "",
        ]

        if not functions and not classes:
            lines.extend([
                "def test_module_execution():",
                "    # Verifies top-level code runs without uncaught exceptions",
                "    import main",
                "    assert main is not None",
                "",
            ])
            return "\n".join(lines)

        for fn in functions:
            fn_name = fn["name"]
            params = fn["parameters"]
            ops = fn.get("operations", [])
            has_division = any("division" in op for op in ops)

            if len(params) == 2:
                p1, p2 = params[0].lower(), params[1].lower()
                # Arithmetic / Math operations
                if any(k in fn_name.lower() for k in ["add", "sum", "plus"]):
                    lines.extend([
                        f"def test_{fn_name}_positive():",
                        f"    assert {fn_name}(2, 3) == 5",
                        "",
                        f"def test_{fn_name}_negative():",
                        f"    assert {fn_name}(-4, -6) == -10",
                        "",
                        f"def test_{fn_name}_zero():",
                        f"    assert {fn_name}(0, 7) == 7",
                        "",
                    ])
                elif any(k in fn_name.lower() for k in ["sub", "diff", "minus"]):
                    lines.extend([
                        f"def test_{fn_name}_positive():",
                        f"    assert {fn_name}(10, 4) == 6",
                        "",
                        f"def test_{fn_name}_negative():",
                        f"    assert {fn_name}(5, 10) == -5",
                        "",
                    ])
                elif any(k in fn_name.lower() for k in ["mul", "prod", "times"]):
                    lines.extend([
                        f"def test_{fn_name}_positive():",
                        f"    assert {fn_name}(3, 4) == 12",
                        "",
                        f"def test_{fn_name}_zero():",
                        f"    assert {fn_name}(5, 0) == 0",
                        "",
                    ])
                elif any(k in fn_name.lower() for k in ["div", "ratio", "quotient"]) or has_division:
                    lines.extend([
                        f"def test_{fn_name}_valid():",
                        f"    assert {fn_name}(10, 2) == 5",
                        "",
                        f"def test_{fn_name}_fraction():",
                        f"    assert {fn_name}(5, 2) == 2.5",
                        "",
                        f"def test_{fn_name}_division_by_zero():",
                        f"    # Tests edge-case zero divisor",
                        f"    with pytest.raises(ZeroDivisionError):",
                        f"        {fn_name}(10, 0)",
                        "",
                    ])
                else:
                    lines.extend([
                        f"def test_{fn_name}_basic():",
                        f"    result = {fn_name}(10, 5)",
                        f"    assert result is not None",
                        "",
                    ])
            elif len(params) == 1:
                p = params[0].lower()
                if any(k in p for k in ["text", "str", "s", "name", "word"]):
                    lines.extend([
                        f"def test_{fn_name}_string():",
                        f"    assert {fn_name}('hello') is not None",
                        "",
                        f"def test_{fn_name}_empty_string():",
                        f"    assert {fn_name}('') is not None",
                        "",
                    ])
                elif any(k in p for k in ["num", "n", "x", "val", "count", "age"]):
                    lines.extend([
                        f"def test_{fn_name}_positive_number():",
                        f"    assert {fn_name}(10) is not None",
                        "",
                        f"def test_{fn_name}_zero():",
                        f"    assert {fn_name}(0) is not None",
                        "",
                    ])
                elif any(k in p for k in ["items", "lst", "arr", "data", "list"]):
                    lines.extend([
                        f"def test_{fn_name}_list():",
                        f"    assert {fn_name}([1, 2, 3]) is not None",
                        "",
                        f"def test_{fn_name}_empty_list():",
                        f"    assert {fn_name}([]) is not None",
                        "",
                    ])
                else:
                    lines.extend([
                        f"def test_{fn_name}_call():",
                        f"    assert {fn_name}(1) is not None",
                        "",
                    ])
            elif len(params) == 0:
                lines.extend([
                    f"def test_{fn_name}_no_args():",
                    f"    result = {fn_name}()",
                    f"    assert result is not None or result is None",
                    "",
                ])
            else:
                dummy_args = ", ".join(["1"] * len(params))
                lines.extend([
                    f"def test_{fn_name}_multi_args():",
                    f"    result = {fn_name}({dummy_args})",
                    f"    assert result is not None",
                    "",
                ])

        return "\n".join(lines)

    def _execute_test_suite(self, source_code: str, test_code: str) -> Dict[str, Any]:
        """
        Runs the test suite against the source code in an isolated directory and extracts results.
        """
        temp_dir = Path(tempfile.mkdtemp(prefix="code_audit_"))
        try:
            main_file = temp_dir / "main.py"
            test_file = temp_dir / "test_main.py"
            main_file.write_text(source_code, encoding="utf-8")
            test_file.write_text(test_code, encoding="utf-8")

            # Environment setup
            env = os.environ.copy()
            env["PYTHONPATH"] = str(temp_dir)
            env["PYTHONDONTWRITEBYTECODE"] = "1"

            # Execute pytest
            cmd = [sys.executable, "-m", "pytest", "test_main.py", "-v", "--tb=short"]
            start_time = time.monotonic()
            
            try:
                proc = subprocess.run(
                    cmd,
                    cwd=str(temp_dir),
                    env=env,
                    capture_output=True,
                    text=True,
                    timeout=self.timeout_seconds,
                    errors="replace",
                )
                duration = round(time.monotonic() - start_time, 3)
                output = proc.stdout + "\n" + proc.stderr
                return_code = proc.returncode
            except subprocess.TimeoutExpired:
                return {
                    "total": 1,
                    "passed": 0,
                    "failed": 1,
                    "duration": self.timeout_seconds,
                    "timed_out": True,
                    "cases": [{
                        "name": "all_tests",
                        "status": "FAILED",
                        "message": f"Execution timed out after {self.timeout_seconds} seconds. Check for infinite loops.",
                    }],
                    "raw_output": f"Timeout expired after {self.timeout_seconds}s.",
                }

            # Parse test results from stdout
            cases = self._parse_pytest_verbose_output(output)
            passed_count = sum(1 for c in cases if c["status"] == "PASSED")
            failed_count = sum(1 for c in cases if c["status"] in ("FAILED", "ERROR"))
            total_count = len(cases)

            return {
                "total": total_count,
                "passed": passed_count,
                "failed": failed_count,
                "duration": duration,
                "return_code": return_code,
                "cases": cases,
                "raw_output": output[-5000:],
            }

        except Exception as exc:
            return {
                "total": 1,
                "passed": 0,
                "failed": 1,
                "duration": 0.0,
                "cases": [{
                    "name": "test_runner_error",
                    "status": "ERROR",
                    "message": str(exc),
                }],
                "raw_output": str(exc),
            }
        finally:
            shutil.rmtree(temp_dir, ignore_errors=True)

    def _parse_pytest_verbose_output(self, output: str) -> List[Dict[str, Any]]:
        cases = []
        # Pattern for: test_main.py::test_name PASSED / FAILED / ERROR
        line_regex = re.compile(r"test_main\.py::(\w+)\s+(PASSED|FAILED|ERROR)", re.IGNORECASE)
        
        # Collect failure snippets
        failure_blocks: Dict[str, str] = {}
        current_failure = None
        failure_lines = []

        for line in output.splitlines():
            match = line_regex.search(line)
            if match:
                cases.append({
                    "name": match.group(1),
                    "status": match.group(2).upper(),
                    "message": f"Test {match.group(1)} {match.group(2).lower()}.",
                })

            if line.startswith("_ ") and line.endswith(" _"):
                if current_failure and failure_lines:
                    failure_blocks[current_failure] = "\n".join(failure_lines).strip()
                current_failure = line.strip("_ ")
                failure_lines = []
            elif current_failure:
                if line.startswith("===") or line.startswith("FAILED test_main.py"):
                    failure_blocks[current_failure] = "\n".join(failure_lines).strip()
                    current_failure = None
                    failure_lines = []
                else:
                    failure_lines.append(line)

        if current_failure and failure_lines:
            failure_blocks[current_failure] = "\n".join(failure_lines).strip()

        # Enhance failed cases with failure snippets
        for c in cases:
            if c["status"] in ("FAILED", "ERROR"):
                fn_name = c["name"]
                diag = failure_blocks.get(fn_name, "")
                if diag:
                    c["details"] = diag
                    # Extract last error line
                    err_lines = [l for l in diag.splitlines() if ":" in l and ("Error" in l or "assert" in l)]
                    if err_lines:
                        c["message"] = err_lines[-1].strip()

        # If no regex matched but returncode != 0 (e.g. collection error)
        if not cases and ("ERROR" in output or "SyntaxError" in output or "ModuleNotFoundError" in output):
            cases.append({
                "name": "collection_or_import_error",
                "status": "ERROR",
                "message": output.strip().splitlines()[-1] if output.strip() else "Error running tests",
                "details": output[-2000:],
            })

        return cases

    def _ast_to_str(self, node: ast.AST) -> str:
        try:
            return ast.unparse(node)
        except Exception:
            if isinstance(node, ast.Name):
                return node.id
            if isinstance(node, ast.Constant):
                return repr(node.value)
            return node.__class__.__name__
