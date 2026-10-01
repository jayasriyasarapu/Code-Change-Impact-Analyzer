from django.contrib import admin
from django.urls import path, include
from django.views.generic import TemplateView
from drf_spectacular.views import (
    SpectacularAPIView,
    SpectacularRedocView,
    SpectacularSwaggerView,
)

from apps.analysis.views import CodeAnalysisAPIView

urlpatterns = [
    path("", TemplateView.as_view(template_name="frontend/index.html"), name="frontend"),
    path("admin/", admin.site.urls),
    # API Documentation (OpenAPI 3 / Swagger / Redoc)
    path("api/schema/", SpectacularAPIView.as_view(), name="schema"),
    path("api/docs/", SpectacularSwaggerView.as_view(url_name="schema"), name="swagger-ui"),
    path("api/redoc/", SpectacularRedocView.as_view(url_name="schema"), name="redoc"),
    # Core API endpoints v1
    path("api/v1/auth/", include("apps.authentication.urls")),
    path("api/v1/projects/", include("apps.projects.urls")),
    path("api/v1/projects/<uuid:project_id>/analyses/", include("apps.analysis.urls")),
    path("api/v1/code/analyze/", CodeAnalysisAPIView.as_view(), name="code-analyze"),
]
