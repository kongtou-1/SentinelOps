from functools import lru_cache
from pathlib import Path

from fastapi.responses import JSONResponse
from sqlalchemy.exc import SQLAlchemyError

import app.repositories.system as _repository
from app.core.config import get_settings

# Used only when the migration scripts are not shipped with the runtime image
# (for example a slim worker image). The live head is read from Alembic so a
# new migration cannot silently turn readiness into a permanent 503.
FALLBACK_REVISION = "0049_askdata_stage5"


@lru_cache(maxsize=1)
def head_revision() -> str:
    """Current Alembic head, or the fallback when the scripts are unavailable."""
    try:
        from alembic.config import Config
        from alembic.script import ScriptDirectory

        root = Path(__file__).resolve().parents[2]
        config = Config(str(root / "alembic.ini"))
        config.set_main_option("script_location", str(root / "migrations"))
        return ScriptDirectory.from_config(config).get_current_head() or FALLBACK_REVISION
    except Exception:  # noqa: BLE001 - readiness must never fail on this lookup
        return FALLBACK_REVISION


def live():
    return {"status": "ok"}


def ready(db):
    try:
        revision = _repository.ready_query(db)
        if revision != head_revision():
            return JSONResponse(status_code=503, content={"status": "not_ready"})
    except SQLAlchemyError:
        return JSONResponse(status_code=503, content={"status": "not_ready"})
    return {"status": "ok", "database": "ok"}


def system_status(db, user):
    revision = _repository.system_status_query(db)
    return {"service": get_settings().app_name, "database": "ok", "revision": revision}
