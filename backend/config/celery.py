import os

from celery import Celery
from celery.signals import worker_process_init, worker_process_shutdown

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")

app = Celery("newscrawler")
app.config_from_object("django.conf:settings", namespace="CELERY")
app.autodiscover_tasks()


@worker_process_init.connect
def _celery_worker_process_init(**_kwargs):
    """Keep Django ORM usable: never leave a Playwright asyncio loop on this thread."""
    # Defensive: drop any inherited / leftover loop from parent process.
    try:
        import asyncio

        try:
            loop = asyncio.get_event_loop_policy().get_event_loop()
        except RuntimeError:
            return
        if loop is not None and not loop.is_running():
            loop.close()
    except Exception:  # noqa: BLE001
        pass


@worker_process_shutdown.connect
def _celery_worker_process_shutdown(**_kwargs):
    try:
        from apps.integrations.searx.google_dork_browser import close_google_browser

        close_google_browser()
    except Exception:  # noqa: BLE001
        pass


@app.task(bind=True, ignore_result=True)
def debug_task(self):
    """Diagnostics helper for Celery connectivity."""
    print(f"Request: {self.request!r}")
