import uuid
from django.conf import settings
from django.db import models
from apps.projects.models import Project, CodeComponent


class AnalysisJob(models.Model):
    class Status(models.TextChoices):
        PENDING = "PENDING", "Pending"
        RUNNING = "RUNNING", "Running"
        COMPLETED = "COMPLETED", "Completed"
        FAILED = "FAILED", "Failed"
        CANCELLED = "CANCELLED", "Cancelled"

    class AnalysisType(models.TextChoices):
        COMMIT = "COMMIT", "Commit"
        BRANCH_DIFF = "BRANCH_DIFF", "Branch Diff"
        CUSTOM_DIFF = "CUSTOM_DIFF", "Custom Diff"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    project = models.ForeignKey(
        Project,
        on_delete=models.CASCADE,
        related_name="analysis_jobs",
    )
    initiated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="analysis_jobs",
    )
    status = models.CharField(
        max_length=20,
        choices=Status.choices,
        default=Status.PENDING,
        db_index=True,
    )
    analysis_type = models.CharField(
        max_length=20,
        choices=AnalysisType.choices,
        default=AnalysisType.COMMIT,
    )
    target_commit_hash = models.CharField(max_length=64, blank=True, default="")
    base_commit_hash = models.CharField(max_length=64, blank=True, default="")
    diff_content = models.TextField(blank=True, default="")
    progress_percent = models.PositiveSmallIntegerField(default=0)
    stage_message = models.CharField(max_length=255, blank=True, default="Initialized")
    error_message = models.TextField(blank=True, default="")
    traceback_log = models.TextField(blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)
    started_at = models.DateTimeField(null=True, blank=True)
    completed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "analysis_jobs"
        ordering = ["-created_at"]

    def __str__(self) -> str:
        return f"Job {self.id} [{self.analysis_type}] ({self.status}) for {self.project.name}"


class AnalysisResult(models.Model):
    class RiskScore(models.TextChoices):
        LOW = "LOW", "Low"
        MEDIUM = "MEDIUM", "Medium"
        HIGH = "HIGH", "High"
        CRITICAL = "CRITICAL", "Critical"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    job = models.OneToOneField(
        AnalysisJob,
        on_delete=models.CASCADE,
        related_name="result",
    )
    overall_risk_score = models.CharField(
        max_length=20,
        choices=RiskScore.choices,
        default=RiskScore.LOW,
        db_index=True,
    )
    risk_score_numeric = models.FloatField(default=0.0)
    total_changed_files = models.PositiveIntegerField(default=0)
    total_changed_components = models.PositiveIntegerField(default=0)
    total_impacted_components = models.PositiveIntegerField(default=0)
    total_affected_tests = models.PositiveIntegerField(default=0)
    summary = models.JSONField(default=dict, blank=True)
    risk_breakdown = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "analysis_results"
        ordering = ["-created_at"]

    def __str__(self) -> str:
        return f"Result for Job {self.job_id} - Risk: {self.overall_risk_score}"


class ChangedComponent(models.Model):
    class ChangeType(models.TextChoices):
        ADDED = "ADDED", "Added"
        MODIFIED = "MODIFIED", "Modified"
        DELETED = "DELETED", "Deleted"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    result = models.ForeignKey(
        AnalysisResult,
        on_delete=models.CASCADE,
        related_name="changed_components",
    )
    component = models.ForeignKey(
        CodeComponent,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="changes",
    )
    file_path = models.CharField(max_length=1000)
    name = models.CharField(max_length=255)
    qualified_name = models.CharField(max_length=500)
    change_type = models.CharField(
        max_length=20,
        choices=ChangeType.choices,
        default=ChangeType.MODIFIED,
    )
    lines_added = models.PositiveIntegerField(default=0)
    lines_deleted = models.PositiveIntegerField(default=0)
    start_line = models.IntegerField(default=0)
    end_line = models.IntegerField(default=0)
    diff_snippet = models.TextField(blank=True, default="")

    class Meta:
        db_table = "changed_components"
        ordering = ["file_path", "start_line"]

    def __str__(self) -> str:
        return f"[{self.change_type}] {self.qualified_name} in {self.file_path}"


class ImpactedComponent(models.Model):
    class ImpactType(models.TextChoices):
        DIRECT = "DIRECT", "Direct Impact"
        TRANSITIVE = "TRANSITIVE", "Transitive Impact"

    class RiskLevel(models.TextChoices):
        LOW = "LOW", "Low"
        MEDIUM = "MEDIUM", "Medium"
        HIGH = "HIGH", "High"
        CRITICAL = "CRITICAL", "Critical"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    result = models.ForeignKey(
        AnalysisResult,
        on_delete=models.CASCADE,
        related_name="impacted_components",
    )
    component = models.ForeignKey(
        CodeComponent,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="impacts",
    )
    file_path = models.CharField(max_length=1000)
    name = models.CharField(max_length=255)
    qualified_name = models.CharField(max_length=500)
    impact_type = models.CharField(
        max_length=20,
        choices=ImpactType.choices,
        default=ImpactType.TRANSITIVE,
    )
    depth = models.PositiveSmallIntegerField(default=1)
    dependency_path = models.JSONField(default=list, blank=True)
    is_test = models.BooleanField(default=False, db_index=True)
    reason = models.TextField()
    risk_level = models.CharField(
        max_length=20,
        choices=RiskLevel.choices,
        default=RiskLevel.MEDIUM,
    )

    class Meta:
        db_table = "impacted_components"
        ordering = ["-is_test", "depth", "qualified_name"]

    def __str__(self) -> str:
        test_tag = " [TEST]" if self.is_test else ""
        return f"{self.qualified_name} (Depth: {self.depth}){test_tag}"
