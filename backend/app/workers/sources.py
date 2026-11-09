import logging
import time
from datetime import UTC, datetime
from pathlib import Path

import app.repositories.worker_sources as _repository
from app.core.config import get_settings
from app.core.secrets import cipher
from app.db.session import SessionLocal, engine
from app.services.operations.platform import pulse
from app.services.sources.store import SourceUnavailable, build_snapshot

logger = logging.getLogger(__name__)
HEARTBEAT = Path("/tmp/source-worker.heartbeat")


def sync_once():
    with SessionLocal() as db:
        snapshot = _repository.sync_once_query(db)
        if snapshot is None:
            return False
        identifier, request = snapshot.id, snapshot.request
        app = _repository.sync_once_query_2(db, snapshot)
        snapshot.status, snapshot.started_at = "running", datetime.now(UTC)
        credentials, error = None, None
        from app.repositories.access import active_user
        from app.services.access_control import Authorization

        actor = active_user(db, snapshot.requested_by)
        if actor is None or not Authorization(db, actor).allows(
            "sources:sync", "application", str(app.id)
        ):
            error = "source_authorization_revoked"
        elif app.deleted_at or not app.enabled:
            error = "application_disabled"
        elif request["source"] != "local" and app.git_url != request.get("git_url"):
            error = "source_configuration_changed"
        elif request["source"] != "local" and (
            app.svn_url != request.get("svn_url")
            or (app.svn_url and app.svn_username != request.get("svn_username"))
        ):
            error = "source_configuration_changed"
        elif app.svn_url and app.encrypted_svn_password and request["source"] != "local":
            try:
                credentials = (
                    app.svn_username,
                    cipher().decrypt(app.encrypted_svn_password.encode()).decode(),
                )
            except Exception:
                credentials = None
        elif app.encrypted_git_token:
            try:
                credentials = (
                    app.git_username,
                    cipher()
                    .decrypt(
                        app.encrypted_git_token.encode(),
                    )
                    .decode(),
                )
            except Exception:
                # Local fallback remains available when a Git key cannot be decrypted.
                credentials = None
        db.commit()
    result = None
    try:
        if error:
            raise SourceUnavailable(error)
        result = build_snapshot(identifier, request, credentials)
    except SourceUnavailable as exc:
        error = str(exc)
    except Exception as exc:
        logger.error("Source capture failed (%s)", type(exc).__name__)
        error = "source_capture_failed"
    with SessionLocal() as db:
        snapshot = _repository.sync_once_query_3(db, identifier)
        actor = active_user(db, snapshot.requested_by)
        if actor is None or not Authorization(db, actor).allows(
            "sources:sync", "application", str(snapshot.application_id)
        ):
            result, error = None, "source_authorization_revoked"
        if result:
            for key, value in result.items():
                setattr(snapshot, key, value)
        else:
            snapshot.status, snapshot.error_code = "failed", error
            snapshot.data_gaps = [error]
        snapshot.finished_at = datetime.now(UTC)
        _repository.sync_once_query_4(db, identifier, snapshot)
        db.commit()
    return True


def main():
    logging.basicConfig(level=logging.INFO)
    with engine.connect() as owner:
        if not _repository.main_query(owner):
            raise SystemExit("Another source worker is active")
        owner.commit()
        with SessionLocal() as db:
            _repository.main_query_2(db)
            db.commit()
        logger.info("Source worker ready; input=%s", get_settings().source_input_dir)
        while True:
            pulse("source")
            HEARTBEAT.write_text(str(time.time()), encoding="utf-8")
            _repository.main_query_3(owner)
            owner.commit()
            if not sync_once():
                time.sleep(5)


if __name__ == "__main__":
    main()
