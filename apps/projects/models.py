import uuid
from django.conf import settings
from django.db import models
from django.utils.text import slugify


class Project(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="projects",
    )
    name = models.CharField(max_length=200, db_index=True)
    slug = models.SlugField(max_length=220, blank=True)
    description = models.TextField(blank=True, default="")
    language = models.CharField(max_length=50, default="python")
    default_branch = models.CharField(max_length=100, default="main")
    storage_path = models.CharField(max_length=500, blank=True, default="")
    is_archived = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "projects"
        ordering = ["-created_at"]
        constraints = [
            models.UniqueConstraint(fields=["owner", "name"], name="unique_user_project_name")
        ]

    def save(self, *args, **kwargs):
        if not self.slug:
            self.slug = slugify(self.name)
        super().save(*args, **kwargs)

    def __str__(self) -> str:
        return f"{self.name} (Owner: {self.owner.username})"


class Repository(models.Model):
    class Status(models.TextChoices):
        IDLE = "IDLE", "Idle"
        CLONING = "CLONING", "Cloning"
        READY = "READY", "Ready"
        FAILED = "FAILED", "Failed"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    project = models.OneToOneField(
        Project,
        on_delete=models.CASCADE,
        related_name="repository",
    )
    remote_url = models.CharField(max_length=500, blank=True, default="")
    is_cloned = models.BooleanField(default=False)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.IDLE)
    default_branch = models.CharField(max_length=100, default="main")
    last_synced_at = models.DateTimeField(null=True, blank=True)
    error_message = models.TextField(blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "repositories"

    def __str__(self) -> str:
        return f"Repo for {self.project.name} ({self.remote_url or 'Local/Uploaded'})"


class Commit(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    repository = models.ForeignKey(
        Repository,
        on_delete=models.CASCADE,
        related_name="commits",
    )
    commit_hash = models.CharField(max_length=64, db_index=True)
    author_name = models.CharField(max_length=255, blank=True, default="")
    author_email = models.CharField(max_length=255, blank=True, default="")
    message = models.TextField(blank=True, default="")
    committed_at = models.DateTimeField(null=True, blank=True)
    parent_hashes = models.JSONField(default=list, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "commits"
        ordering = ["-committed_at", "-created_at"]
        constraints = [
            models.UniqueConstraint(fields=["repository", "commit_hash"], name="unique_repo_commit")
        ]

    def __str__(self) -> str:
        return f"{self.commit_hash[:8]} - {self.message[:40]}"


class CodeFile(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    project = models.ForeignKey(
        Project,
        on_delete=models.CASCADE,
        related_name="files",
    )
    file_path = models.CharField(max_length=1000, db_index=True)
    language = models.CharField(max_length=50, default="python")
    checksum = models.CharField(max_length=64, blank=True, default="")
    is_test_file = models.BooleanField(default=False, db_index=True)
    total_lines = models.PositiveIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "code_files"
        ordering = ["file_path"]
        constraints = [
            models.UniqueConstraint(fields=["project", "file_path"], name="unique_project_file")
        ]

    def __str__(self) -> str:
        return f"{self.file_path} ({'Test' if self.is_test_file else 'Source'})"


class CodeComponent(models.Model):
    class ComponentType(models.TextChoices):
        MODULE = "MODULE", "Module"
        CLASS = "CLASS", "Class"
        FUNCTION = "FUNCTION", "Function"
        METHOD = "METHOD", "Method"
        VARIABLE = "VARIABLE", "Variable"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    code_file = models.ForeignKey(
        CodeFile,
        on_delete=models.CASCADE,
        related_name="components",
    )
    name = models.CharField(max_length=255, db_index=True)
    qualified_name = models.CharField(max_length=500, db_index=True)
    component_type = models.CharField(
        max_length=20,
        choices=ComponentType.choices,
        default=ComponentType.FUNCTION,
    )
    parent_component = models.ForeignKey(
        "self",
        null=True,
        blank=True,
        on_delete=models.CASCADE,
        related_name="child_components",
    )
    start_line = models.PositiveIntegerField()
    end_line = models.PositiveIntegerField()
    docstring = models.TextField(blank=True, default="")
    parameters = models.JSONField(default=list, blank=True)
    is_test = models.BooleanField(default=False, db_index=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "code_components"
        ordering = ["code_file", "start_line"]
        indexes = [
            models.Index(fields=["qualified_name"]),
            models.Index(fields=["component_type"]),
        ]

    def __str__(self) -> str:
        return f"[{self.component_type}] {self.qualified_name} (L{self.start_line}-{self.end_line})"


class ComponentDependency(models.Model):
    class DependencyType(models.TextChoices):
        CALLS = "CALLS", "Calls"
        INHERITS = "INHERITS", "Inherits"
        IMPORTS = "IMPORTS", "Imports"
        REFERENCES = "REFERENCES", "References"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    source_component = models.ForeignKey(
        CodeComponent,
        on_delete=models.CASCADE,
        related_name="outgoing_dependencies",
    )
    target_component = models.ForeignKey(
        CodeComponent,
        on_delete=models.CASCADE,
        related_name="incoming_dependencies",
    )
    dependency_type = models.CharField(
        max_length=20,
        choices=DependencyType.choices,
        default=DependencyType.CALLS,
    )
    confidence = models.FloatField(default=1.0)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "component_dependencies"
        constraints = [
            models.UniqueConstraint(
                fields=["source_component", "target_component", "dependency_type"],
                name="unique_dependency_edge",
            )
        ]

    def __str__(self) -> str:
        return f"{self.source_component.name} --({self.dependency_type})--> {self.target_component.name}"
