"""FastAPI application: Second Nature engineering workbench API (OpenAPI docs at /docs)."""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from app import MODEL_VERSION, __version__
from app.api import analysis, calculators, climate, projects, reference, simulations
from app.core.config import REPO_ROOT, get_settings
from app.core.errors import SecondNatureError
from app.core.logging import configure_logging
from app.database.session import init_db

log = logging.getLogger("second_nature")


@asynccontextmanager
async def lifespan(app: FastAPI):
    s = get_settings()
    configure_logging(s.log_level)
    init_db()
    log.info(f"Second Nature {__version__} (model {MODEL_VERSION}) started; data dir {s.data_dir}")
    yield


app = FastAPI(title="Second Nature API", version=__version__, lifespan=lifespan,
              description="Biomimetic passive-cooling and sustainable data-center design platform. Preliminary design and decision "
                          "support — not a substitute for detailed engineering. All units SI unless stated.")
app.add_middleware(CORSMiddleware, allow_origins=get_settings().cors_origins, allow_methods=["*"], allow_headers=["*"])


@app.exception_handler(SecondNatureError)
async def domain_error(_: Request, exc: SecondNatureError):
    return JSONResponse(status_code=exc.status_code, content={"error": exc.code, "message": exc.message, "details": exc.details})


@app.exception_handler(RequestValidationError)
async def validation_error(_: Request, exc: RequestValidationError):
    errs = [{"loc": list(e.get("loc", [])), "msg": e.get("msg"), "type": e.get("type")} for e in exc.errors()]
    return JSONResponse(status_code=422, content={"error": "validation_failed", "message": "request validation failed", "details": errs})


for r in (reference.router, projects.router, climate.router, simulations.router, analysis.router, calculators.router):
    app.include_router(r)

# Serve the built frontend (single-process deployment) when present.
DIST = Path(REPO_ROOT) / "frontend" / "dist"
if DIST.exists():
    app.mount("/assets", StaticFiles(directory=DIST / "assets"), name="assets")

    @app.get("/{path:path}", include_in_schema=False)
    async def spa(path: str):
        if path.startswith("api/") or path == "api":
            return JSONResponse(status_code=404, content={"error": "not_found", "message": f"/{path} not found"})
        target = DIST / path
        if path and target.is_file() and DIST in target.resolve().parents:
            return FileResponse(target)
        return FileResponse(DIST / "index.html")
