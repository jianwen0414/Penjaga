"""``/eval/v1``: the Lab's evaluation view (operator role only).

Bodies are contract lab types (``lab/eval_api.schema.json``). Responses are checked against the
JSON Schema before they leave (a violation is a server bug: 500). The full report of a complete
run is served as JSON (``/report``) and Markdown (``/report.md``).

Errors: 401 without the operator token; 404 unknown run or report; 422 invalid request.
"""

from __future__ import annotations

import asyncio
import contextlib
import secrets
from collections.abc import AsyncIterator
from typing import Annotated, Any

from fastapi import Depends, FastAPI, HTTPException, Request, status
from fastapi.responses import FileResponse, JSONResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from penjaga_contract import schema_registry
from penjaga_contract.lab import EvalRun, EvalRunRequest, EvalSuite
from penjaga_eval.service import EvalRequestError, EvalService, UnknownRunError
from penjaga_sim import GENERATOR_VERSION

PREFIX = "/eval/v1"
BEARER = HTTPBearer(auto_error=False)  # module level: FastAPI resolves annotations here


class ContractViolationError(RuntimeError):
    """The service produced a response that breaks the contract (a bug, never the caller's)."""


def document(doc: Any, definition: str, *, many: bool = False, code: int = 200) -> JSONResponse:
    for item in doc if many else [doc]:
        problems = schema_registry.error_messages("lab", "eval_api", item, definition)
        if problems:
            raise ContractViolationError(f"{definition}: {'; '.join(problems)}")
    return JSONResponse(doc, status_code=code)


def create_app(service: EvalService, *, operator_token: str, autostart: bool = True) -> FastAPI:
    if len(operator_token) < 16:
        raise ValueError("the operator token needs at least 16 characters")

    @contextlib.asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        stop = asyncio.Event()
        task = asyncio.create_task(service.serve(stop)) if autostart else None
        try:
            yield
        finally:
            stop.set()
            if task is not None:
                await task

    app = FastAPI(
        title="Penjaga evaluation service (M11)",
        version="1.0.0",
        lifespan=lifespan,
        openapi_url=f"{PREFIX}/openapi.json",
        docs_url=f"{PREFIX}/docs",
        redoc_url=None,
    )

    def operator(
        credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(BEARER)],
    ) -> None:
        token = "" if credentials is None else credentials.credentials
        if not secrets.compare_digest(token.encode(), operator_token.encode()):
            raise HTTPException(
                status.HTTP_401_UNAUTHORIZED,
                "operator bearer token required",
                headers={"WWW-Authenticate": "Bearer"},
            )

    auth = [Depends(operator)]

    def error(code: int) -> Any:
        async def handler(_: Request, exc: Exception) -> JSONResponse:
            return JSONResponse({"detail": str(exc)}, status_code=code)

        return handler

    app.add_exception_handler(UnknownRunError, error(status.HTTP_404_NOT_FOUND))
    app.add_exception_handler(EvalRequestError, error(status.HTTP_422_UNPROCESSABLE_CONTENT))

    @app.get(f"{PREFIX}/health")
    async def health() -> dict[str, Any]:
        return {"status": "ok", "generator": GENERATOR_VERSION, "running": service.running}

    @app.get(f"{PREFIX}/suites", dependencies=auth, response_model=list[EvalSuite])
    async def suites() -> JSONResponse:
        docs = [service.suite(name) for name in service.suite_names()]
        return document(docs, "EvalSuite", many=True)

    @app.post(f"{PREFIX}/runs", dependencies=auth, status_code=201, response_model=EvalRun)
    async def post_run(body: EvalRunRequest) -> JSONResponse:
        doc = service.create(body.suite, body.split.value, body.detectors, body.limit)
        return document(doc, "EvalRun", code=201)

    @app.get(f"{PREFIX}/runs", dependencies=auth, response_model=list[EvalRun])
    async def runs() -> JSONResponse:
        return document(service.runs(), "EvalRun", many=True)

    @app.get(f"{PREFIX}/runs/{{run_id}}", dependencies=auth, response_model=EvalRun)
    async def run(run_id: str) -> JSONResponse:
        return document(service.get(run_id), "EvalRun")

    @app.get(f"{PREFIX}/runs/{{run_id}}/report", dependencies=auth)
    async def report(run_id: str) -> FileResponse:
        return FileResponse(
            service.report_path(run_id, "report.json"), media_type="application/json"
        )

    @app.get(f"{PREFIX}/runs/{{run_id}}/report.md", dependencies=auth)
    async def report_md(run_id: str) -> FileResponse:
        path = service.report_path(run_id, "report.md")
        return FileResponse(path, media_type="text/markdown; charset=utf-8")

    return app
