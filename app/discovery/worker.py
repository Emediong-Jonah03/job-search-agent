import logging

from app.db.models import AuditEvent

from app.config import get_settings
from app.db.session import SessionLocal
from app.discovery.adapters import AshbyAdapter, GreenhouseAdapter, LeverAdapter
from app.discovery.adapters.public_feeds import (
    JobicyAdapter,
    RemotiveAdapter,
    RemoteOKAdapter,
    WeWorkRemotelyAdapter,
)
from app.discovery.http import get_json, get_text
from app.discovery.models import BoardConfig
from app.discovery.service import check_live_link, fresh_jobs, parse_board_config
from app.discovery.repository import persist_discovery
from app.matching.scorer import score_job
from app.profile.loader import load_profile
from app.verification.service import verify_unassessed_discovered_jobs

logger = logging.getLogger(__name__)

ADAPTERS = {
    "greenhouse": GreenhouseAdapter,
    "lever": LeverAdapter,
    "ashby": AshbyAdapter,
    "remoteok": RemoteOKAdapter,
    "remotive": RemotiveAdapter,
    "jobicy": JobicyAdapter,
    "weworkremotely": WeWorkRemotelyAdapter,
}


async def discover_configured_boards(_: dict) -> dict[str, int]:
    settings = get_settings()
    board_configs = parse_board_config(settings.discovery_boards)
    profile = load_profile(settings.profile_path)
    fetched_count = fresh_count = created_count = duplicate_count = rejected_count = 0
    errors = 0

    for board_config in board_configs:
        adapter_class = ADAPTERS[board_config.source]
        adapter = adapter_class(get_json, get_text) if board_config.source == "weworkremotely" else adapter_class(get_json)
        try:
            jobs = await adapter.fetch(board_config.board)
        except Exception as exc:
            errors += 1
            logger.warning("Job source fetch failed", extra={"source": board_config.source, "error_type": type(exc).__name__})
            with SessionLocal() as session:
                with session.begin():
                    session.add(AuditEvent(
                        event_type="job_source.fetch_failed",
                        actor=f"discovery:{board_config.source}",
                        payload={"board": board_config.board, "error_type": type(exc).__name__},
                    ))
            continue
        with SessionLocal() as session:
            with session.begin():
                session.add(AuditEvent(
                    event_type="job_source.fetch_completed",
                    actor=f"discovery:{board_config.source}",
                    payload={"board": board_config.board, "jobs_returned": len(jobs)},
                ))
        fetched_count += len(jobs)
        eligible, too_old_or_undated = fresh_jobs(jobs)
        rejected_count += len(too_old_or_undated)
        fresh_count += len(eligible)
        for job in eligible:
            check = await check_live_link(job)
            if not check.accessible:
                rejected_count += 1
                continue
            result = score_job(job, profile)
            with SessionLocal() as session:
                _, created = persist_discovery(session, job, check, result)
            if created:
                created_count += 1
            else:
                duplicate_count += 1

    assessed_count = verify_unassessed_discovered_jobs(SessionLocal)
    return {
        "boards": len(board_configs),
        "fetched": fetched_count,
        "fresh": fresh_count,
        "created": created_count,
        "duplicates": duplicate_count,
        "excluded": rejected_count,
        "source_errors": errors,
        "verification_assessments": assessed_count,
    }

