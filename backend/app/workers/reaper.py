"""Periodic lease reaper hook; invoke from a scheduler/worker process."""
from datetime import datetime, timezone
from sqlalchemy import select
from sqlalchemy.orm import Session
from app.models import RunnerLease, TestRun
from app.routers.runner import MAX_ATTEMPTS


def recover_expired_leases(db: Session) -> int:
    now=datetime.now(timezone.utc);leases=db.scalars(select(RunnerLease).where(RunnerLease.lease_expires_at<now).with_for_update()).all()
    count=0
    for lease in leases:
        run=db.get(TestRun,lease.test_run_id)
        if run and run.status in ("claimed","running"):
            if lease.attempt_count>=MAX_ATTEMPTS:run.status="failed";run.error_message="Runner lease expired after maximum attempts";run.completed_at=now
            else:run.status="queued"
            db.delete(lease);count+=1
    db.commit();return count
