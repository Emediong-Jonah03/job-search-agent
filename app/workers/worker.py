from arq.connections import RedisSettings

from app.config import get_settings


class WorkerSettings:
    functions = []
    redis_settings = RedisSettings.from_dsn(get_settings().redis_url)
    max_tries = 3
    retry_jobs = True
    job_timeout = 300

