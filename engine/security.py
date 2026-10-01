import os
import re
import zipfile
from pathlib import Path
from typing import Optional, List


class SecurityError(Exception):
    """Base security exception."""
    pass


class PathTraversalError(SecurityError):
    """Raised when an attempt to escape the designated sandbox directory is detected."""
    pass


class ResourceLimitExceededError(SecurityError):
    """Raised when uploaded content exceeds safe file count or size limits."""
    pass


# Allowed Git URL patterns: HTTPS, HTTP, SSH/git@
GIT_URL_REGEX = re.compile(
    r"^(https?://([a-zA-Z0-9_\-\.]+@)?[a-zA-Z0-9_\-\.]+(:\d+)?/[a-zA-Z0-9_\-\./]+(\.git)?|"
    r"git@[a-zA-Z0-9_\-\.]+:([a-zA-Z0-9_\-\./]+)(\.git)?)$"
)

# Git ref (branch/tag/commit SHA): alphanumeric, hyphens, underscores, slashes, dots
GIT_REF_REGEX = re.compile(r"^[a-zA-Z0-9_\-\./]{1,120}$")


def validate_git_url(url: str) -> str:
    """
    Validate that the remote git URL matches safe URI schemes and contains no shell metacharacters.
    """
    cleaned = url.strip()
    if not cleaned:
        raise SecurityError("Git URL cannot be empty.")
    if not GIT_URL_REGEX.match(cleaned):
        raise SecurityError(f"Invalid or unsupported Git repository URL format: '{cleaned}'")
    # Prohibit dangerous characters like semicolons, pipes, backticks, spaces
    if any(c in cleaned for c in [";", "|", "&", "`", "$", "\n", "\r", " "]):
        raise SecurityError("Git URL contains forbidden metacharacters.")
    return cleaned


def validate_git_ref(ref: str) -> str:
    """
    Validate branch, tag, or commit hash.
    """
    cleaned = ref.strip()
    if not cleaned:
        raise SecurityError("Git ref cannot be empty.")
    if cleaned.startswith("-") or ".." in cleaned or not GIT_REF_REGEX.match(cleaned):
        raise SecurityError(f"Invalid or unsafe Git reference name: '{cleaned}'")
    return cleaned


def validate_safe_path(base_dir: Path, target_path: Path) -> Path:
    """
    Ensure target_path resolves strictly inside base_dir, preventing path traversal attacks.
    """
    resolved_base = base_dir.resolve()
    resolved_target = target_path.resolve()

    try:
        resolved_target.relative_to(resolved_base)
    except ValueError:
        raise PathTraversalError(
            f"Path traversal detected: '{target_path}' is outside base directory '{base_dir}'"
        )

    return resolved_target


def safe_extract_zip(
    zip_path: Path,
    target_dir: Path,
    max_total_size: int = 100 * 1024 * 1024,  # 100 MB default
    max_file_count: int = 5000,
) -> List[Path]:
    """
    Safely extract a ZIP archive while protecting against Zip Slip (path traversal),
    decompression bombs (Zip bombs), and symbolic link redirection.
    """
    target_dir.mkdir(parents=True, exist_ok=True)
    resolved_target_dir = target_dir.resolve()

    extracted_files: List[Path] = []
    total_uncompressed_bytes = 0
    file_count = 0

    with zipfile.ZipFile(zip_path, "r") as archive:
        for member in archive.infolist():
            file_count += 1
            if file_count > max_file_count:
                raise ResourceLimitExceededError(
                    f"Archive exceeds maximum allowable file count of {max_file_count}"
                )

            total_uncompressed_bytes += member.file_size
            if total_uncompressed_bytes > max_total_size:
                raise ResourceLimitExceededError(
                    f"Archive uncompressed size exceeds maximum allowable limit of {max_total_size} bytes"
                )

            # Prevent absolute paths or path traversal in filenames
            member_path = member.filename
            if member_path.startswith("/") or member_path.startswith("\\") or ".." in member_path.split("/"):
                raise PathTraversalError(f"Dangerous path in archive member: '{member.filename}'")

            destination_path = (resolved_target_dir / member_path).resolve()
            validate_safe_path(resolved_target_dir, destination_path)

            # Extract regular files and directories safely
            if member.is_dir():
                destination_path.mkdir(parents=True, exist_ok=True)
            else:
                destination_path.parent.mkdir(parents=True, exist_ok=True)
                with archive.open(member) as source, open(destination_path, "wb") as dest:
                    # Write in 64KB chunks
                    while chunk := source.read(65536):
                        dest.write(chunk)
                extracted_files.append(destination_path)

    return extracted_files
