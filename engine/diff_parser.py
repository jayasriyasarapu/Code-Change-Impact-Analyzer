import re
from dataclasses import dataclass, field
from typing import List, Optional, Tuple, Dict


@dataclass
class DiffHunk:
    old_start: int
    old_count: int
    new_start: int
    new_count: int
    header: str
    added_lines: List[int] = field(default_factory=list)
    deleted_lines: List[int] = field(default_factory=list)
    content: str = ""


@dataclass
class FileDiff:
    old_path: Optional[str]
    new_path: Optional[str]
    change_type: str  # 'ADDED', 'DELETED', 'MODIFIED', 'RENAMED'
    hunks: List[DiffHunk] = field(default_factory=list)
    added_line_numbers: List[int] = field(default_factory=list)
    deleted_line_numbers: List[int] = field(default_factory=list)
    raw_diff: str = ""

    @property
    def display_path(self) -> str:
        return self.new_path or self.old_path or "unknown"


class DiffParser:
    """
    Parses unified git diffs into structured file diffs and hunks,
    mapping line changes to 1-indexed line numbers.
    """

    HUNK_HEADER_REGEX = re.compile(
        r"^@@\s+-(\d+)(?:,(\d+))?\s+\+(\d+)(?:,(\d+))?\s+@@(.*)$"
    )

    def parse(self, diff_text: str) -> List[FileDiff]:
        if not diff_text or not diff_text.strip():
            return []

        lines = diff_text.splitlines()
        file_diffs: List[FileDiff] = []
        current_file: Optional[FileDiff] = None
        current_hunk: Optional[DiffHunk] = None
        current_file_lines: List[str] = []

        old_line_cursor = 0
        new_line_cursor = 0

        i = 0
        while i < len(lines):
            line = lines[i]

            # Detect git diff header: diff --git a/... b/...
            if line.startswith("diff --git "):
                if current_file:
                    if current_hunk:
                        current_file.hunks.append(current_hunk)
                        current_hunk = None
                    current_file.raw_diff = "\n".join(current_file_lines)
                    file_diffs.append(current_file)
                    current_file_lines = []

                # Extract paths from header
                parts = line.split(" ")
                old_p = parts[2][2:] if len(parts) > 2 and parts[2].startswith("a/") else None
                new_p = parts[3][2:] if len(parts) > 3 and parts[3].startswith("b/") else None

                current_file = FileDiff(
                    old_path=old_p,
                    new_path=new_p,
                    change_type="MODIFIED",
                )
                current_file_lines.append(line)
                i += 1
                continue

            if not current_file:
                # Handle patches that lack `diff --git` but start with `--- a/` and `+++ b/`
                if line.startswith("--- "):
                    old_p = line[4:].strip()
                    if old_p.startswith("a/"):
                        old_p = old_p[2:]
                    current_file = FileDiff(
                        old_path=old_p if old_p != "/dev/null" else None,
                        new_path=None,
                        change_type="MODIFIED",
                    )
                    current_file_lines.append(line)
                i += 1
                continue

            current_file_lines.append(line)

            # Metadata lines
            if line.startswith("new file mode "):
                current_file.change_type = "ADDED"
            elif line.startswith("deleted file mode "):
                current_file.change_type = "DELETED"
            elif line.startswith("rename from "):
                current_file.change_type = "RENAMED"
                current_file.old_path = line[12:].strip()
            elif line.startswith("rename to "):
                current_file.new_path = line[10:].strip()
            elif line.startswith("--- "):
                old_p = line[4:].strip()
                if old_p.startswith("a/"):
                    old_p = old_p[2:]
                if old_p == "/dev/null":
                    current_file.change_type = "ADDED"
                    current_file.old_path = None
                else:
                    current_file.old_path = old_p
            elif line.startswith("+++ "):
                new_p = line[4:].strip()
                if new_p.startswith("b/"):
                    new_p = new_p[2:]
                if new_p == "/dev/null":
                    current_file.change_type = "DELETED"
                    current_file.new_path = None
                else:
                    current_file.new_path = new_p

            # Hunk header: @@ -10,5 +12,8 @@
            elif line.startswith("@@"):
                if current_hunk:
                    current_file.hunks.append(current_hunk)

                match = self.HUNK_HEADER_REGEX.match(line)
                if match:
                    old_start = int(match.group(1))
                    old_count = int(match.group(2)) if match.group(2) else 1
                    new_start = int(match.group(3))
                    new_count = int(match.group(4)) if match.group(4) else 1
                    hunk_header = match.group(5).strip()

                    current_hunk = DiffHunk(
                        old_start=old_start,
                        old_count=old_count,
                        new_start=new_start,
                        new_count=new_count,
                        header=hunk_header,
                        content=line,
                    )
                    old_line_cursor = old_start
                    new_line_cursor = new_start

            # Diff content inside a hunk
            elif current_hunk is not None:
                current_hunk.content += "\n" + line
                if line.startswith("+"):
                    current_hunk.added_lines.append(new_line_cursor)
                    current_file.added_line_numbers.append(new_line_cursor)
                    new_line_cursor += 1
                elif line.startswith("-"):
                    current_hunk.deleted_lines.append(old_line_cursor)
                    current_file.deleted_line_numbers.append(old_line_cursor)
                    old_line_cursor += 1
                elif line.startswith(" ") or line == "":
                    old_line_cursor += 1
                    new_line_cursor += 1

            i += 1

        if current_file:
            if current_hunk:
                current_file.hunks.append(current_hunk)
            current_file.raw_diff = "\n".join(current_file_lines)
            file_diffs.append(current_file)

        return file_diffs
