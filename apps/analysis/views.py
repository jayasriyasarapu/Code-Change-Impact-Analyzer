from django.db import transaction
from django.utils import timezone
from rest_framework import generics, status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from engine.dependency.graph import SymbolDependencyGraph
from engine.diff_parser import DiffParser
from engine.git_service import GitService, GitServiceError
from engine.impact.analyzer import ImpactAnalyzer
from engine.parser.base import ParsedSymbol, SymbolType
from django.conf import settings

from apps.projects.models import CodeComponent, ComponentDependency, Project
from .models import AnalysisJob, AnalysisResult, ChangedComponent, ImpactedComponent
from .serializers import AnalysisCreateSerializer, AnalysisJobSerializer


class ProjectAnalysisListCreateView(generics.ListCreateAPIView):
    permission_classes = [IsAuthenticated]
    serializer_class = AnalysisJobSerializer

    def get_project(self):
        return Project.objects.get(id=self.kwargs["project_id"], owner=self.request.user)

    def get_queryset(self):
        return AnalysisJob.objects.filter(project=self.get_project()).select_related("result")

    def create(self, request, *args, **kwargs):
        project = self.get_project()
        input_serializer = AnalysisCreateSerializer(data=request.data)
        input_serializer.is_valid(raise_exception=True)
        values = input_serializer.validated_data
        job = AnalysisJob.objects.create(
            project=project,
            initiated_by=request.user,
            analysis_type=values.get("analysis_type", AnalysisJob.AnalysisType.CUSTOM_DIFF),
            target_commit_hash=values.get("target_commit_hash", ""),
            base_commit_hash=values.get("base_commit_hash", ""),
            diff_content=values.get("diff_content", ""),
            status=AnalysisJob.Status.RUNNING,
            started_at=timezone.now(),
            stage_message="Preparing dependency graph",
        )

        try:
            diff_content = self._resolve_diff(project, values)
            self._run_job(job, diff_content)
        except (GitServiceError, ValueError, OSError) as exc:
            job.status = AnalysisJob.Status.FAILED
            job.error_message = str(exc)
            job.stage_message = "Analysis failed"
            job.completed_at = timezone.now()
            job.save(update_fields=["status", "error_message", "stage_message", "completed_at"])
            return Response(AnalysisJobSerializer(job).data, status=status.HTTP_400_BAD_REQUEST)

        return Response(AnalysisJobSerializer(job).data, status=status.HTTP_201_CREATED)

    def _resolve_diff(self, project, values):
        analysis_type = values.get("analysis_type")
        if analysis_type == AnalysisJob.AnalysisType.CUSTOM_DIFF:
            return values["diff_content"]
        repository = getattr(project, "repository", None)
        if not repository or not repository.is_cloned:
            raise ValueError("Sync a repository before running a commit or branch analysis.")
        service = GitService(settings.STORAGE_ROOT / str(project.id))
        if analysis_type == AnalysisJob.AnalysisType.COMMIT:
            return service.get_commit_diff(values["target_commit_hash"])
        return service.get_branch_diff(values["base_commit_hash"], values["target_commit_hash"])

    @transaction.atomic
    def _run_job(self, job, diff_content):
        project = job.project
        graph = self._build_graph(project)
        report = ImpactAnalyzer().analyze(DiffParser().parse(diff_content), graph)
        assessment = report.risk_assessment
        result = AnalysisResult.objects.create(
            job=job,
            overall_risk_score=assessment.level.value,
            risk_score_numeric=assessment.score,
            total_changed_files=report.total_changed_files,
            total_changed_components=report.total_changed_components,
            total_impacted_components=report.total_impacted_components,
            total_affected_tests=report.total_affected_tests,
            summary=report.summary,
            risk_breakdown=assessment.breakdown,
        )
        ChangedComponent.objects.bulk_create([
            ChangedComponent(
                result=result,
                file_path=item.file_path,
                name=item.name,
                qualified_name=item.qualified_name,
                change_type=item.change_type,
                lines_added=item.lines_added,
                lines_deleted=item.lines_deleted,
                start_line=item.start_line,
                end_line=item.end_line,
                diff_snippet=item.diff_snippet,
            )
            for item in report.changed_components
        ])
        ImpactedComponent.objects.bulk_create([
            ImpactedComponent(
                result=result,
                file_path=item.file_path,
                name=item.name,
                qualified_name=item.qualified_name,
                impact_type=item.impact_type,
                depth=item.depth,
                dependency_path=item.dependency_path,
                is_test=item.is_test,
                reason=item.reason,
                risk_level=item.risk_level,
            )
            for item in report.impacted_components
        ])
        job.status = AnalysisJob.Status.COMPLETED
        job.progress_percent = 100
        job.stage_message = "Analysis complete"
        job.completed_at = timezone.now()
        job.save(update_fields=["status", "progress_percent", "stage_message", "completed_at"])

    def _build_graph(self, project):
        components = list(CodeComponent.objects.filter(code_file__project=project).select_related("code_file", "parent_component"))
        graph = SymbolDependencyGraph()
        symbols = []
        for component in components:
            try:
                symbol_type = SymbolType(component.component_type)
            except ValueError:
                symbol_type = SymbolType.FUNCTION
            symbols.append(ParsedSymbol(
                name=component.name,
                qualified_name=component.qualified_name,
                symbol_type=symbol_type,
                file_path=component.code_file.file_path,
                start_line=component.start_line,
                end_line=component.end_line,
                docstring=component.docstring,
                parameters=component.parameters,
                parent_name=component.parent_component.name if component.parent_component else None,
                is_test=component.is_test,
            ))
        graph.build_from_symbols(symbols)
        component_by_id = {str(component.id): component for component in components}
        for dependency in ComponentDependency.objects.filter(source_component_id__in=component_by_id):
            graph.add_dependency(
                dependency.source_component.qualified_name,
                dependency.target_component.qualified_name,
                dependency.dependency_type,
                dependency.confidence,
            )
        return graph


class ProjectAnalysisDetailView(generics.RetrieveAPIView):
    permission_classes = [IsAuthenticated]
    serializer_class = AnalysisJobSerializer
    lookup_url_kwarg = "analysis_id"

    def get_queryset(self):
        return AnalysisJob.objects.filter(
            project__id=self.kwargs["project_id"],
            project__owner=self.request.user,
        ).select_related("result")


class CodeAnalysisAPIView(generics.GenericAPIView):
    """
    Analyzes raw Python code to extract functions, provide plain-English explanations of
    what the code does, detect runtime risks/edge cases, generate pytest test cases, and
    execute them live in a safe sandbox.
    """
    permission_classes = []  # Allows any user or guest to analyze pasted code immediately
    authentication_classes = []

    def post(self, request, *args, **kwargs):
        from engine.code_analyzer import CodeAnalyzer
        
        source_code = request.data.get("source_code", "")
        test_code = request.data.get("test_code", "")
        
        if not source_code or not source_code.strip():
            return Response(
                {"detail": "Please paste or type Python code in the source code field."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        analyzer = CodeAnalyzer(timeout_seconds=settings.ANALYSIS_LIMITS.get("TEST_TIMEOUT_SECONDS", 15))
        report = analyzer.analyze(source_code, custom_tests=test_code if test_code.strip() else None)
        return Response(report, status=status.HTTP_200_OK)
