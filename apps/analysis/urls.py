from django.urls import path

from .views import ProjectAnalysisDetailView, ProjectAnalysisListCreateView

app_name = "analysis"

urlpatterns = [
	path("", ProjectAnalysisListCreateView.as_view(), name="analysis-list-create"),
	path("<uuid:analysis_id>/", ProjectAnalysisDetailView.as_view(), name="analysis-detail"),
]
