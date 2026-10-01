import hashlib
import shutil
from pathlib import Path

from django.conf import settings
from django.db import IntegrityError, transaction
from django.utils import timezone
from rest_framework.parsers import FormParser, MultiPartParser
from rest_framework import decorators, response, status, viewsets
from rest_framework.permissions import IsAuthenticated

from engine.audit import AuditError, SafeProjectAudit
from engine.git_service import GitService, GitServiceError

from .models import CodeComponent, CodeFile, Commit, ComponentDependency, Project, Repository
from .serializers import CommitSerializer, ProjectSerializer, RepositorySerializer


class ProjectViewSet(viewsets.ModelViewSet):
    serializer_class = ProjectSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        return Project.objects.filter(owner=self.request.user).select_related("repository")

    def perform_create(self, serializer):
        from rest_framework import serializers as drf_serializers
        try:
            serializer.save(owner=self.request.user)
        except IntegrityError:
            raise drf_serializers.ValidationError({"name": ["You already have a project with this name. Please choose a different name."]})

    @decorators.action(detail=True, methods=["get", "put", "patch"], url_path="repository")
    def repository(self, request, pk=None):
        project = self.get_object()
        repository, _ = Repository.objects.get_or_create(
            project=project,
            defaults={"default_branch": project.default_branch},
        )
        if request.method == "GET":
            return response.Response(RepositorySerializer(repository).data)

        serializer = RepositorySerializer(repository, data=request.data, partial=request.method == "PATCH")
        serializer.is_valid(raise_exception=True)
        serializer.save()
        return response.Response(serializer.data)

    @decorators.action(detail=True, methods=["get"], url_path="commits")
    def commits(self, request, pk=None):
        project = self.get_object()
        commits = Commit.objects.filter(repository__project=project)
        return response.Response(CommitSerializer(commits, many=True).data)

    @decorators.action(
        detail=True,
        methods=["post"],
        url_path="upload",
        parser_classes=[MultiPartParser, FormParser],
    )
    def upload(self, request, pk=None):
        project = self.get_object()
        archive = request.FILES.get("archive")
        if not archive:
            return response.Response({"detail": "Upload a .zip archive in the 'archive' field."}, status=status.HTTP_400_BAD_REQUEST)
        project_root = settings.STORAGE_ROOT / str(project.id)
        if project_root.exists():
            shutil.rmtree(project_root)
        try:
            scanner = SafeProjectAudit(
                project_root,
                max_file_size=settings.ANALYSIS_LIMITS["MAX_FILE_SIZE_BYTES"],
                max_files=settings.ANALYSIS_LIMITS["MAX_TOTAL_FILES"],
            )
            scanner.extract_zip(archive, project_root)
            scan = scanner.scan()
            self._persist_scan(project, scan)
        except (AuditError, OSError, ValueError) as exc:
            return response.Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        project.storage_path = str(project_root)
        project.save(update_fields=["storage_path", "updated_at"])
        return response.Response({
            "message": "Project uploaded and scanned.",
            "source_files": len(scan["files"]),
            "test_files": sum(1 for item in scan["files"] if item["is_test_file"]),
            "symbols": len(scan["symbols"]),
        }, status=status.HTTP_201_CREATED)

    @decorators.action(detail=True, methods=["post"], url_path="audit")
    def audit(self, request, pk=None):
        project = self.get_object()
        archive = request.FILES.get("archive")
        source_code = request.data.get("source_code", "")
        test_code = request.data.get("test_code", "")
        source_path = request.data.get("source_path", "main.py")
        test_path = request.data.get("test_path", "test_main.py")
        project_root = settings.STORAGE_ROOT / str(project.id)
        try:
            scanner = SafeProjectAudit(
                project_root,
                max_file_size=settings.ANALYSIS_LIMITS["MAX_FILE_SIZE_BYTES"],
                max_files=settings.ANALYSIS_LIMITS["MAX_TOTAL_FILES"],
            )
            if archive:
                if project_root.exists():
                    shutil.rmtree(project_root)
                scanner.extract_zip(archive, project_root)
                project.storage_path = str(project_root)
                project.save(update_fields=["storage_path", "updated_at"])
            elif source_code.strip():
                if project_root.exists():
                    shutil.rmtree(project_root)
                project_root.mkdir(parents=True, exist_ok=True)
                self._write_pasted_file(scanner, source_path, source_code)
                if test_code.strip():
                    self._write_pasted_file(scanner, test_path, test_code)
                project.storage_path = str(project_root)
                project.save(update_fields=["storage_path", "updated_at"])
            if not project_root.exists():
                raise AuditError("Paste source code, upload a project archive, or sync a repository before auditing.")
            scan = scanner.scan()
            self._persist_scan(project, scan)
            test_run = scanner.run_tests(settings.ANALYSIS_LIMITS["TEST_TIMEOUT_SECONDS"])
            audit_report = scanner.build_report(scan, test_run).to_dict()
        except (AuditError, OSError, ValueError) as exc:
            return response.Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)

        from apps.analysis.models import AnalysisJob, AnalysisResult
        from apps.analysis.serializers import AnalysisJobSerializer

        failures = audit_report["tests_failed"] + audit_report["tests_errors"]
        risk_level = "CRITICAL" if audit_report["tests_errors"] else ("HIGH" if failures else ("MEDIUM" if not audit_report["tests_collected"] else "LOW"))
        risk_score = 90.0 if risk_level == "CRITICAL" else (70.0 if failures else 10.0)
        job = AnalysisJob.objects.create(
            project=project,
            initiated_by=request.user,
            analysis_type=AnalysisJob.AnalysisType.CUSTOM_DIFF,
            status=AnalysisJob.Status.COMPLETED,
            progress_percent=100,
            stage_message="Audit complete",
            started_at=timezone.now(),
            completed_at=timezone.now(),
        )
        AnalysisResult.objects.create(
            job=job,
            overall_risk_score=risk_level,
            risk_score_numeric=risk_score,
            total_changed_files=audit_report["source_files"],
            total_changed_components=audit_report["symbols"],
            total_impacted_components=len(audit_report["failures"]),
            total_affected_tests=audit_report["tests_failed"],
            summary={"audit": audit_report, "conclusion": audit_report["conclusion"]},
            risk_breakdown={"test_failures": audit_report["tests_failed"], "test_errors": audit_report["tests_errors"]},
        )
        return response.Response(AnalysisJobSerializer(job).data, status=status.HTTP_201_CREATED)

    def _write_pasted_file(self, scanner, relative_path, content):
        relative = Path(str(relative_path))
        if relative.is_absolute() or ".." in relative.parts or relative.suffix.lower() != ".py":
            raise AuditError("Pasted files must be safe relative Python paths ending in .py.")
        encoded = str(content).encode("utf-8")
        if len(encoded) > scanner.max_file_size:
            raise AuditError(f"Pasted file '{relative_path}' exceeds the per-file size limit.")
        target = (scanner.project_root / relative).resolve()
        if scanner.project_root not in target.parents:
            raise AuditError("Pasted file path is unsafe.")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(encoded)

    @transaction.atomic
    def _persist_scan(self, project, scan):
        CodeFile.objects.filter(project=project).delete()
        file_models = {}
        component_models = {}
        for file_data in scan["files"]:
            path = Path(settings.STORAGE_ROOT / str(project.id) / file_data["file_path"])
            checksum = hashlib.sha256(path.read_bytes()).hexdigest() if path.exists() else ""
            file_model = CodeFile.objects.create(
                project=project,
                file_path=file_data["file_path"],
                language=file_data["language"],
                checksum=checksum,
                is_test_file=file_data["is_test_file"],
                total_lines=file_data["total_lines"],
            )
            file_models[file_data["file_path"]] = file_model
            for symbol in file_data["symbols"]:
                component_models[symbol.qualified_name] = CodeComponent.objects.create(
                    code_file=file_model,
                    name=symbol.name,
                    qualified_name=symbol.qualified_name,
                    component_type=symbol.symbol_type.value,
                    start_line=symbol.start_line,
                    end_line=symbol.end_line,
                    docstring=symbol.docstring,
                    parameters=symbol.parameters,
                    is_test=symbol.is_test,
                )
        for symbol in scan["symbols"]:
            parent_name = symbol.qualified_name.rsplit(".", 1)[0] if symbol.parent_name else None
            if parent_name in component_models:
                component_models[symbol.qualified_name].parent_component = component_models[parent_name]
                component_models[symbol.qualified_name].save(update_fields=["parent_component"])
        edges = []
        for source, target, data in scan["graph"].graph.edges(data=True):
            if source in component_models and target in component_models:
                edges.append(ComponentDependency(
                    source_component=component_models[source],
                    target_component=component_models[target],
                    dependency_type=data.get("dependency_type", "CALLS"),
                    confidence=data.get("confidence", 1.0),
                ))
        ComponentDependency.objects.bulk_create(edges, ignore_conflicts=True)

    @decorators.action(detail=True, methods=["post"], url_path="sync")
    def sync(self, request, pk=None):
        project = self.get_object()
        remote_url = request.data.get("remote_url")
        branch = request.data.get("branch", project.default_branch)
        if not remote_url:
            return response.Response({"detail": "remote_url is required."}, status=status.HTTP_400_BAD_REQUEST)

        repository, _ = Repository.objects.get_or_create(project=project)
        repository.remote_url = remote_url
        repository.default_branch = branch
        repository.status = Repository.Status.CLONING
        repository.error_message = ""
        repository.save(update_fields=["remote_url", "default_branch", "status", "error_message", "updated_at"])

        try:
            repo_dir = settings.STORAGE_ROOT / str(project.id)
            git_service = GitService(repo_dir)
            repo = git_service.clone_repository(
                remote_url,
                branch=branch,
                timeout=settings.ANALYSIS_LIMITS["GIT_TIMEOUT_SECONDS"],
            )
            scanner = SafeProjectAudit(
                repo_dir,
                max_file_size=settings.ANALYSIS_LIMITS["MAX_FILE_SIZE_BYTES"],
                max_files=settings.ANALYSIS_LIMITS["MAX_TOTAL_FILES"],
            )
            scan = scanner.scan()
            self._persist_scan(project, scan)
            commits_data = git_service.get_recent_commits()
            Commit.objects.filter(repository=repository).delete()
            Commit.objects.bulk_create([
                Commit(repository=repository, **commit_data) for commit_data in commits_data
            ])
            repository.is_cloned = True
            repository.status = Repository.Status.READY
            repository.last_synced_at = repo.head.commit.committed_datetime if repo.head else None
            repository.save(update_fields=["is_cloned", "status", "last_synced_at", "updated_at"])
            project.storage_path = str(repo_dir)
            project.save(update_fields=["storage_path", "updated_at"])
        except (GitServiceError, OSError, ValueError) as exc:
            repository.status = Repository.Status.FAILED
            repository.error_message = str(exc)
            repository.save(update_fields=["status", "error_message", "updated_at"])
            return response.Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)

        return response.Response(RepositorySerializer(repository).data)
