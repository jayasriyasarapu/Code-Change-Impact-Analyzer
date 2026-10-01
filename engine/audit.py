from __future__ import annotations

import ast
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import zipfile
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

from engine.dependency.graph import SymbolDependencyGraph
from engine.parser.base import ParsedSymbol
from engine.parser.registry import ParserRegistry


class AuditError(Exception):
    """Raised when an uploaded project cannot be safely audited."""


@dataclass
class FailureRecord:
    test_name: str
    file_path: str
    line_number: Optional[int]
    error_type: str
    message: str
    traceback: str = ""
    likely_cause: str = ""
    suggested_fix: str = ""
    suggested_tests: List[str] = field(default_factory=list)
    related_components: List[str] = field(default_factory=list)


@dataclass
class AuditReport:
    source_files: int
    test_files: int
    symbols: int
    tests_collected: int
    tests_passed: int
    tests_failed: int
    tests_errors: int
    tests_skipped: int
    duration_seconds: float
    command: List[str]
    output: str
    failures: List[FailureRecord]
    coverage: Dict[str, Any]
    conclusion: str

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


class SafeProjectAudit:
    """Scan and test a Python project with bounded, non-network subprocess execution."""

    ignored_directories = {
        ".git", ".hg", ".svn", "__pycache__", ".pytest_cache", ".mypy_cache",
        ".venv", "venv", "node_modules", "dist", "build", "staticfiles",
    }

    def __init__(self, project_root: Path, max_file_size: int = 5 * 1024 * 1024, max_files: int = 5000):
        self.project_root = project_root.resolve()
        self.max_file_size = max_file_size
        self.max_files = max_files

    def extract_zip(self, archive, destination: Path) -> Path:
        if getattr(archive, "size", 0) > self.max_file_size * 20:
            raise AuditError("The uploaded archive is larger than the configured limit.")
        destination.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(archive) as source:
            members = [member for member in source.infolist() if not member.is_dir()]
            if len(members) > self.max_files:
                raise AuditError("The archive contains too many files.")
            for member in members:
                relative = Path(member.filename)
                if relative.is_absolute() or ".." in relative.parts:
                    raise AuditError("The archive contains an unsafe path.")
                target = (destination / relative).resolve()
                if destination.resolve() not in target.parents:
                    raise AuditError("The archive contains an unsafe path.")
                if member.file_size > self.max_file_size:
                    raise AuditError(f"File '{member.filename}' exceeds the per-file size limit.")
                target.parent.mkdir(parents=True, exist_ok=True)
                with source.open(member) as source_file, target.open("wb") as target_file:
                    shutil.copyfileobj(source_file, target_file, length=1024 * 1024)
        return self._flatten_single_directory(destination)

    def scan(self) -> Dict[str, Any]:
        files: List[Dict[str, Any]] = []
        symbols: List[ParsedSymbol] = []
        for path in self._source_files():
            relative = path.relative_to(self.project_root).as_posix()
            content = path.read_text(encoding="utf-8", errors="replace")
            parser = ParserRegistry.get_parser_for_file(relative)
            parsed = parser.parse_source(relative, content) if parser else []
            files.append({
                "file_path": relative,
                "language": parser.supported_language if parser else path.suffix.lstrip("."),
                "is_test_file": bool(parser and parser.is_test_file(relative)),
                "total_lines": len(content.splitlines()),
                "content": content,
                "symbols": parsed,
            })
            symbols.extend(parsed)
        graph = SymbolDependencyGraph()
        graph.build_from_symbols(symbols)
        return {"files": files, "symbols": symbols, "graph": graph}

    def run_tests(self, timeout_seconds: int = 120) -> Dict[str, Any]:
        command = self._test_command()
        coverage_enabled = self._coverage_enabled()
        if coverage_enabled and command[1:3] == ["-m", "pytest"]:
            command = [sys.executable, "-m", "coverage", "run", "--branch", "-m", "pytest", "-q", "--tb=short"]
        env = os.environ.copy()
        env.update({
            "PYTHONDONTWRITEBYTECODE": "1",
            "PYTHONUNBUFFERED": "1",
            "PYTHONPATH": str(self.project_root),
            "NO_PROXY": "*",
            "no_proxy": "*",
        })
        coverage_data = self.project_root / ".audit_coverage"
        env["COVERAGE_FILE"] = str(coverage_data)
        started = __import__("time").monotonic()
        try:
            process = subprocess.Popen(
                command,
                cwd=self.project_root,
                env=env,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding="utf-8",
                errors="replace",
                creationflags=getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0),
            )
            try:
                output, _ = process.communicate(timeout=timeout_seconds)
            except subprocess.TimeoutExpired:
                if os.name == "nt":
                    subprocess.run(["taskkill", "/F", "/T", "/PID", str(process.pid)], capture_output=True)
                else:
                    process.kill()
                output, _ = process.communicate()
                output += f"\nTest run timed out after {timeout_seconds} seconds."
                return {"command": command, "return_code": 124, "duration": __import__("time").monotonic() - started, "output": output, "timed_out": True, "coverage": {}}
        except OSError as exc:
            raise AuditError(f"Unable to start test runner: {exc}") from exc
        return {
            "command": command,
            "return_code": process.returncode,
            "duration": __import__("time").monotonic() - started,
            "output": output,
            "timed_out": False,
            "coverage": self._collect_coverage(coverage_enabled, env, coverage_data),
        }

    def build_report(self, scan: Dict[str, Any], test_run: Dict[str, Any]) -> AuditReport:
        output = test_run["output"]
        failures = self._parse_failures(output, scan["graph"])
        counts = self._parse_counts(output)
        tests_failed = counts["failed"] or len(failures)
        tests_passed = counts["passed"]
        tests_errors = counts["errors"]
        tests_skipped = counts["skipped"]
        if test_run["timed_out"]:
            conclusion = "The test run exceeded the safety timeout. Review the command and possible infinite loops before rerunning."
        elif tests_failed or tests_errors:
            conclusion = f"{tests_failed + tests_errors} test failure(s) need attention. Start with the listed file and line, then add the suggested regression tests."
        elif tests_passed:
            conclusion = "All discovered tests passed. The scan found no runtime test failures, but review coverage gaps before shipping."
        else:
            conclusion = "No tests were discovered. Add a test suite before relying on this result."
        return AuditReport(
            source_files=len(scan["files"]),
            test_files=sum(1 for item in scan["files"] if item["is_test_file"]),
            symbols=len(scan["symbols"]),
            tests_collected=counts["total"],
            tests_passed=tests_passed,
            tests_failed=tests_failed,
            tests_errors=tests_errors,
            tests_skipped=tests_skipped,
            duration_seconds=round(test_run["duration"], 3),
            command=test_run["command"],
            output=output[-20000:],
            failures=failures,
            coverage=self._coverage_summary(scan, output, test_run.get("coverage", {})),
            conclusion=conclusion,
        )

    def _source_files(self) -> Iterable[Path]:
        count = 0
        for path in self.project_root.rglob("*.py"):
            if any(part in self.ignored_directories for part in path.parts):
                continue
            if path.stat().st_size > self.max_file_size:
                continue
            count += 1
            if count > self.max_files:
                raise AuditError("The project contains too many Python files.")
            yield path

    def _test_command(self) -> List[str]:
        try:
            __import__("pytest")
            return [sys.executable, "-m", "pytest", "-q", "--tb=short"]
        except ImportError:
            return [sys.executable, "-m", "unittest", "discover", "-v"]

    def _coverage_enabled(self) -> bool:
        try:
            __import__("coverage")
            __import__("pytest")
            return True
        except ImportError:
            return False

    def _collect_coverage(self, enabled: bool, env: Dict[str, str], data_file: Path) -> Dict[str, Any]:
        if not enabled or not data_file.exists():
            return {}
        report_file = self.project_root / ".audit_coverage.json"
        try:
            subprocess.run(
                [sys.executable, "-m", "coverage", "json", "-o", str(report_file)],
                cwd=self.project_root,
                env=env,
                check=True,
                capture_output=True,
                text=True,
            )
            data = json.loads(report_file.read_text(encoding="utf-8"))
            totals = data.get("totals", {})
            return {
                "available": True,
                "percent_covered": totals.get("percent_covered", 0),
                "covered_lines": totals.get("covered_lines", 0),
                "total_lines": totals.get("num_statements", 0),
                "missing_lines": totals.get("missing_lines", 0),
            }
        except (OSError, ValueError, subprocess.SubprocessError, json.JSONDecodeError):
            return {}
        finally:
            data_file.unlink(missing_ok=True)
            report_file.unlink(missing_ok=True)

    def _flatten_single_directory(self, destination: Path) -> Path:
        children = [item for item in destination.iterdir() if item.name not in {"__MACOSX"}]
        if len(children) == 1 and children[0].is_dir():
            nested = children[0]
            for child in nested.iterdir():
                child.rename(destination / child.name)
            nested.rmdir()
        return destination

    def _parse_counts(self, output: str) -> Dict[str, int]:
        counts = {"total": 0, "passed": 0, "failed": 0, "errors": 0, "skipped": 0}
        for value, label in re.findall(r"(\d+)\s+(passed|failed|error|errors|skipped|deselected)", output.lower()):
            number = int(value)
            if label == "passed": counts["passed"] = number
            elif label == "failed": counts["failed"] = number
            elif label in {"error", "errors"}: counts["errors"] = number
            elif label == "skipped": counts["skipped"] = number
        counts["total"] = sum(counts[key] for key in ("passed", "failed", "errors", "skipped"))
        return counts

    def _parse_failures(self, output: str, graph: SymbolDependencyGraph) -> List[FailureRecord]:
        records: List[FailureRecord] = []
        matches = list(re.finditer(r"(?m)^_{3,}\s+(.+?)\s+_{3,}$", output))
        for index, match in enumerate(matches):
            block = output[match.start():matches[index + 1].start() if index + 1 < len(matches) else len(output)]
            test_name = match.group(1).strip()
            locations = re.findall(r"(?m)^(.+?\.py):(\d+)(?::|$)", block)
            root_text = str(self.project_root).replace("\\", "/").lower()
            location = next((item for item in locations if root_text in item[0].replace("\\", "/").lower()), None)
            if location is None:
                location = next((item for item in locations if not any(part in item[0].replace("\\", "/") for part in ("site-packages/", "_pytest/", "/venv/", "\\venv\\"))), locations[0] if locations else None)
            error = re.search(r"(?m)^E\s+([A-Za-z_][\w.]*(?:Error|Exception)):\s*(.*)$", block)
            if not error:
                error = re.search(r"(?m)^E\s+(.+)$", block)
            file_path = location[0].replace("\\", "/") if location else "unknown"
            line_number = int(location[1]) if location else None
            error_type = error.group(1) if error and error.lastindex and error.lastindex > 1 else "TestFailure"
            message = error.group(2).strip() if error and error.lastindex and error.lastindex > 1 else (error.group(1).strip() if error else "Test failed")
            cause, fix, tests = self._suggestions(error_type, message)
            related = [name for name, symbol in graph.symbols.items() if self._same_file(symbol.file_path, file_path)][:8]
            records.append(FailureRecord(test_name, file_path, line_number, error_type, message, block[-6000:], cause, fix, tests, related))
        return records

    def _suggestions(self, error_type: str, message: str) -> tuple[str, str, List[str]]:
        if "AssertionError" in error_type:
            return ("The actual value differs from the expected value in the assertion.", "Inspect the implementation and the expected value; change the code only if the requirement is wrong.", ["Add a regression test for the failing input and one boundary case."])
        if "NameError" in error_type:
            return ("The test or implementation references a name that is not defined in that scope.", "Define the name or import it explicitly, then add a test that exercises the corrected path.", ["Add a test that imports and calls the affected public function."])
        if "TypeError" in error_type:
            return ("A value has an incompatible type or a function received the wrong arguments.", "Check the function signature and normalize or validate the input before calling it.", ["Add valid, invalid, and boundary-type test cases."])
        if "ModuleNotFoundError" in error_type or "ImportError" in error_type:
            return ("A required module cannot be imported in the test environment.", "Add the dependency to the project environment or correct the import path.", ["Add a clean-environment import smoke test."])
        return (f"The test runner reported {error_type}: {message}", "Read the traceback from the first project frame and make the smallest behavior-focused fix.", ["Add a regression test that reproduces this exact failure."])

    def _coverage_summary(self, scan: Dict[str, Any], output: str, measured: Dict[str, Any]) -> Dict[str, Any]:
        source_files = [item["file_path"] for item in scan["files"] if not item["is_test_file"]]
        test_files = [item["file_path"] for item in scan["files"] if item["is_test_file"]]
        test_content = "\n".join(item["content"] for item in scan["files"] if item["is_test_file"])
        tested_files = [path for path in source_files if Path(path).stem in test_content]
        untested_files = [path for path in source_files if path not in tested_files]
        gap = "Install coverage and rerun to measure executed lines."
        if untested_files:
            gap += f" No direct test reference was found for: {', '.join(untested_files[:8])}."
        summary = {
            "available": False,
            "source_files": len(source_files),
            "test_files": len(test_files),
            "tested_source_files": len(tested_files),
            "untested_source_files": untested_files,
            "gap": gap,
        }
        if measured:
            summary.update(measured)
            summary["gap"] = f"Measured line coverage: {measured.get('percent_covered', 0):.1f}%."
        return summary

    def _same_file(self, first: str, second: str) -> bool:
        return first.replace("\\", "/").lower().endswith(second.replace("\\", "/").lower())
