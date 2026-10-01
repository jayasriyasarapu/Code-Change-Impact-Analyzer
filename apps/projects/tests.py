import io
import shutil
import uuid
import zipfile

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from rest_framework.test import APIClient

from apps.authentication.models import User
from .models import Project


class ProjectAuditApiTests(TestCase):
    def setUp(self):
        suffix = uuid.uuid4().hex[:8]
        self.user = User.objects.create_user(
            username=f"audit_test_{suffix}",
            email=f"audit_test_{suffix}@example.com",
            password="TestPass123!",
        )
        self.project = Project.objects.create(owner=self.user, name=f"Audit Test {suffix}")
        self.client = APIClient(HTTP_HOST="127.0.0.1")
        self.client.force_authenticate(user=self.user)

    def tearDown(self):
        if self.project.storage_path:
            shutil.rmtree(self.project.storage_path, ignore_errors=True)

    def _archive(self, files):
        content = io.BytesIO()
        with zipfile.ZipFile(content, "w") as archive:
            for name, source in files.items():
                archive.writestr(name, source)
        content.seek(0)
        return SimpleUploadedFile("project.zip", content.read(), content_type="application/zip")

    def test_audit_reports_failing_test_location_and_suggestion(self):
        newline = "\n"
        archive = self._archive({
            "calculator.py": newline.join(["def add(left, right):", "    return left - right", ""]),
            "test_calculator.py": newline.join([
                "from calculator import add",
                "",
                "def test_add():",
                "    assert add(2, 3) == 5",
                "",
            ]),
        })

        response = self.client.post(
            f"/api/v1/projects/{self.project.id}/audit/",
            {"archive": archive},
            format="multipart",
        )

        self.assertEqual(response.status_code, 201)
        report = response.data["result"]["summary"]["audit"]
        self.assertEqual(report["tests_failed"], 1)
        self.assertEqual(report["failures"][0]["file_path"], "test_calculator.py")
        self.assertEqual(report["failures"][0]["line_number"], 4)
        self.assertTrue(report["failures"][0]["suggested_tests"])
        self.assertTrue(report["coverage"]["available"])

    def test_audit_rejects_archive_path_traversal(self):
        archive = self._archive({"../unsafe.py": "print('unsafe')"})

        response = self.client.post(
            f"/api/v1/projects/{self.project.id}/audit/",
            {"archive": archive},
            format="multipart",
        )

        self.assertEqual(response.status_code, 400)
        self.assertIn("unsafe path", response.data["detail"])

    def test_audit_accepts_pasted_source_and_tests(self):
        newline = "\n"
        response = self.client.post(
            f"/api/v1/projects/{self.project.id}/audit/",
            {
                "source_path": "main.py",
                "source_code": newline.join(["def add(left, right):", "    return left + right", ""]),
                "test_path": "test_main.py",
                "test_code": newline.join([
                    "from main import add",
                    "",
                    "def test_add():",
                    "    assert add(2, 3) == 5",
                    "",
                ]),
            },
            format="json",
        )

        self.assertEqual(response.status_code, 201)
        report = response.data["result"]["summary"]["audit"]
        self.assertEqual(report["tests_passed"], 1)
        self.assertEqual(report["tests_failed"], 0)
