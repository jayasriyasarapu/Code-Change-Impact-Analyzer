from rest_framework import serializers

from .models import Commit, Project, Repository


class RepositorySerializer(serializers.ModelSerializer):
    class Meta:
        model = Repository
        fields = (
            "id",
            "remote_url",
            "is_cloned",
            "status",
            "default_branch",
            "last_synced_at",
            "error_message",
        )
        read_only_fields = ("id", "is_cloned", "status", "last_synced_at", "error_message")


class CommitSerializer(serializers.ModelSerializer):
    class Meta:
        model = Commit
        fields = (
            "id",
            "commit_hash",
            "author_name",
            "author_email",
            "message",
            "committed_at",
            "parent_hashes",
        )


class ProjectSerializer(serializers.ModelSerializer):
    repository = RepositorySerializer(read_only=True)
    files_count = serializers.IntegerField(source="files.count", read_only=True)
    analyses_count = serializers.IntegerField(source="analysis_jobs.count", read_only=True)

    class Meta:
        model = Project
        fields = (
            "id",
            "name",
            "slug",
            "description",
            "language",
            "default_branch",
            "is_archived",
            "created_at",
            "updated_at",
            "repository",
            "files_count",
            "analyses_count",
        )
        read_only_fields = ("id", "slug", "created_at", "updated_at", "repository", "files_count", "analyses_count")
