import os
from celery import Celery

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "code_impact_analyzer.settings")

app = Celery("code_impact_analyzer")

# Using a string here means the worker doesn't have to serialize
# the configuration object to child processes.
app.config_from_object("django.conf:settings", namespace="CELERY")

# Load task modules from all registered Django apps.
app.autodiscover_tasks()


@app.task(bind=True, ignore_result=True)
def debug_task(self):
    print(f"Request: {self.request!r}")
