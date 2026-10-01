from rest_framework import serializers

from .models import AnalysisJob, AnalysisResult, ChangedComponent, ImpactedComponent


class ChangedComponentSerializer(serializers.ModelSerializer):
    class Meta:
        model = ChangedComponent
        fields = (
            "id",
            "file_path",
            "name",
            "qualified_name",
            "change_type",
            "lines_added",
            "lines_deleted",
            "start_line",
            "end_line",
            "diff_snippet",
            "component",
        )


class ImpactedComponentSerializer(serializers.ModelSerializer):
    class Meta:
        model = ImpactedComponent
        fields = (
            "id",
            "file_path",
            "name",
            "qualified_name",
            "impact_type",
            "depth",
            "dependency_path",
            "is_test",
            "reason",
            "risk_level",
            "component",
        )


class AnalysisResultSerializer(serializers.ModelSerializer):
    changed_components = ChangedComponentSerializer(many=True, read_only=True)
    impacted_components = ImpactedComponentSerializer(many=True, read_only=True)
    affected_tests = serializers.SerializerMethodField()

    class Meta:
        model = AnalysisResult
        fields = (
            "id",
            "overall_risk_score",
            "risk_score_numeric",
            "total_changed_files",
            "total_changed_components",
            "total_impacted_components",
            "total_affected_tests",
            "summary",
            "risk_breakdown",
            "created_at",
            "changed_components",
            "impacted_components",
            "affected_tests",
        )

    def get_affected_tests(self, obj):
        return ImpactedComponentSerializer(
            obj.impacted_components.filter(is_test=True), many=True
        ).data


class AnalysisJobSerializer(serializers.ModelSerializer):
    result = AnalysisResultSerializer(read_only=True)

    class Meta:
        model = AnalysisJob
        fields = (
            "id",
            "project",
            "initiated_by",
            "status",
            "analysis_type",
            "target_commit_hash",
            "base_commit_hash",
            "progress_percent",
            "stage_message",
            "error_message",
            "created_at",
            "started_at",
            "completed_at",
            "result",
        )
        read_only_fields = fields


class AnalysisCreateSerializer(serializers.Serializer):
    analysis_type = serializers.ChoiceField(
        choices=AnalysisJob.AnalysisType.choices,
        default=AnalysisJob.AnalysisType.CUSTOM_DIFF,
    )
    target_commit_hash = serializers.CharField(required=False, allow_blank=True)
    base_commit_hash = serializers.CharField(required=False, allow_blank=True)
    diff_content = serializers.CharField(required=False, allow_blank=True)

    def validate(self, attrs):
        analysis_type = attrs.get("analysis_type")
        if analysis_type == AnalysisJob.AnalysisType.CUSTOM_DIFF and not attrs.get("diff_content", "").strip():
            raise serializers.ValidationError({"diff_content": "A unified diff is required for a custom analysis."})
        if analysis_type == AnalysisJob.AnalysisType.COMMIT and not attrs.get("target_commit_hash", "").strip():
            raise serializers.ValidationError({"target_commit_hash": "A commit hash is required."})
        if analysis_type == AnalysisJob.AnalysisType.BRANCH_DIFF and (
            not attrs.get("base_commit_hash", "").strip() or not attrs.get("target_commit_hash", "").strip()
        ):
            raise serializers.ValidationError({"base_commit_hash": "Both base and target refs are required."})
        return attrs
