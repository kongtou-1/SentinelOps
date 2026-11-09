import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from sqlalchemy.exc import SQLAlchemyError

from app.api.router import router
from app.core.config import get_settings
from app.db.session import engine
from app.services.llm.probes import ProbeService
from app.services.monitoring.service_probes import ServiceProbes

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    service = ProbeService()
    app.state.llm_probes = service
    service_probes = ServiceProbes()
    app.state.service_probes = service_probes
    probe_task = (
        asyncio.create_task(service_probes.scheduler())
        if get_settings().service_probe_scheduler_enabled
        else None
    )
    task = (
        asyncio.create_task(service.scheduler())
        if get_settings().llm_probe_scheduler_enabled
        else None
    )
    try:
        yield
    finally:
        if probe_task:
            probe_task.cancel()
            await asyncio.gather(probe_task, return_exceptions=True)
        await service_probes.close()
        if task:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        await service.close()
        engine.dispose()


app = FastAPI(title=get_settings().app_name, version="0.1.0", lifespan=lifespan)


@app.exception_handler(RequestValidationError)
async def validation_error(request: Request, exc: RequestValidationError):
    # FastAPI's default response includes input values, which can expose passwords and tokens.
    return JSONResponse(
        status_code=422,
        content={"detail": [{k: e[k] for k in ("loc", "msg", "type")} for e in exc.errors()]},
    )


@app.exception_handler(SQLAlchemyError)
async def database_error(request: Request, exc: SQLAlchemyError):
    logger.error("Database request failed (%s)", type(exc).__name__)
    return JSONResponse(status_code=503, content={"detail": "Database unavailable"})


app.include_router(router)
