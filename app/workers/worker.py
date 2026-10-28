from datetime import timezone

from arq.connections import RedisSettings
from arq.cron import cron

from app.config import get_settings
from app.discovery.worker import discover_configured_boards
from app.tracking.worker import refresh_follow_up_reminders


class WorkerSettings:
    functions = [discover_configured_boards, refresh_follow_up_reminders]
    cron_jobs = [
        cron(discover_configured_boards, hour=7, minute=0, run_at_startup=True),
        cron(discover_configured_boards, hour=13, minute=0),
        cron(discover_configured_boards, hour=19, minute=0),
        cron(refresh_follow_up_reminders, hour=8, minute=0, run_at_startup=True),
    ]
    timezone = timezone.utc
    redis_settings = RedisSettings.from_dsn(get_settings().redis_url)
    max_tries = 3
    retry_jobs = True
    job_timeout = 300

