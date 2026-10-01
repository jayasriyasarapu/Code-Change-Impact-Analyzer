import os
from pathlib import Path
from typing import List, Dict, Any, Optional
import git
from git.exc import GitCommandError
from .security import validate_git_url, validate_git_ref, SecurityError


class GitServiceError(Exception):
    """Raised when a Git operation fails."""
    pass


class GitService:
    """
    Safe wrapper around Git operations for cloning, fetching,
    and generating unified diffs.
    """

    def __init__(self, repo_dir: Path):
        self.repo_dir = repo_dir

    def clone_repository(
        self,
        remote_url: str,
        branch: str = "main",
        depth: int = 50,
        timeout: int = 120,
    ) -> git.Repo:
        """
        Safely clones a remote repository into the designated directory.
        """
        valid_url = validate_git_url(remote_url)
        valid_branch = validate_git_ref(branch)

        self.repo_dir.mkdir(parents=True, exist_ok=True)

        try:
            repo = git.Repo.clone_from(
                valid_url,
                str(self.repo_dir),
                branch=valid_branch,
                depth=depth,
                kill_after_timeout=timeout,
            )
            return repo
        except GitCommandError as e:
            # Try cloning without explicit branch if branch not found
            try:
                repo = git.Repo.clone_from(
                    valid_url,
                    str(self.repo_dir),
                    depth=depth,
                    kill_after_timeout=timeout,
                )
                return repo
            except GitCommandError as fallback_err:
                raise GitServiceError(f"Failed to clone repository: {fallback_err.stderr or str(fallback_err)}")

    def get_repo(self) -> git.Repo:
        if not (self.repo_dir / ".git").exists():
            raise GitServiceError(f"No git repository found at {self.repo_dir}")
        return git.Repo(str(self.repo_dir))

    def fetch_latest(self) -> None:
        repo = self.get_repo()
        try:
            for remote in repo.remotes:
                remote.fetch()
        except GitCommandError as e:
            raise GitServiceError(f"Failed to fetch updates: {e.stderr or str(e)}")

    def get_recent_commits(self, limit: int = 50) -> List[Dict[str, Any]]:
        repo = self.get_repo()
        commits = []
        try:
            for c in repo.iter_commits(max_count=limit):
                commits.append({
                    "commit_hash": c.hexsha,
                    "author_name": c.author.name if c.author else "",
                    "author_email": c.author.email if c.author else "",
                    "message": c.message.strip(),
                    "committed_at": c.committed_datetime,
                    "parent_hashes": [p.hexsha for p in c.parents],
                })
        except GitCommandError as e:
            raise GitServiceError(f"Failed to retrieve commits: {e.stderr or str(e)}")
        return commits

    def get_commit_diff(self, commit_hash: str) -> str:
        """
        Returns unified diff for the given commit against its parent.
        """
        valid_hash = validate_git_ref(commit_hash)
        repo = self.get_repo()

        try:
            commit = repo.commit(valid_hash)
            if commit.parents:
                parent = commit.parents[0]
                diff = repo.git.diff(f"{parent.hexsha}..{commit.hexsha}", unified=3)
            else:
                # First commit in repository
                diff = repo.git.show(valid_hash, format="", unified=3)
            return diff
        except (GitCommandError, ValueError) as e:
            raise GitServiceError(f"Failed to get diff for commit {commit_hash}: {str(e)}")

    def get_branch_diff(self, base_ref: str, target_ref: str) -> str:
        """
        Returns unified diff between two branches or commit SHAs.
        """
        valid_base = validate_git_ref(base_ref)
        valid_target = validate_git_ref(target_ref)
        repo = self.get_repo()

        try:
            diff = repo.git.diff(f"{valid_base}...{valid_target}", unified=3)
            return diff
        except GitCommandError as e:
            raise GitServiceError(f"Failed to get diff between {base_ref} and {target_ref}: {str(e)}")
