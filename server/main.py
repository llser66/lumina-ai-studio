import asyncio
import json
import logging
import mimetypes
import os
import time
import uuid
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, HTTPException, Query, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from filelock import FileLock
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver

from server.asset_store import create_asset_store
from server.config import ROOT, Settings
from server.db import TERMINAL, Conflict, Database, GlobalQuotaExceeded, QuotaExceeded
from server.middleware import BodyLimitMiddleware
from server.model_provider import ModelRouter
from server.model_routes import router as model_routes
from server.model_service import ModelService
from server.pi_bridge import PiBridge
from server.provider import normalize_image
from server.schemas import Credentials, FavoriteUpdate, GenerationSpec, Registration
from server.security import (
    DUMMY_HASH,
    PASSWORDS,
    current_user,
    digest,
    issue_session,
    mutation_user,
    rate_limit,
)
from server.worker import Worker
from server.workflow import build_graph

logger = logging.getLogger("lumina.api")
# Windows registry associations can map .js to text/plain. Module scripts require
# explicit standard MIME types, independently of the machine's file associations.
for extension, media_type in {
    ".js": "text/javascript",
    ".mjs": "text/javascript",
    ".css": "text/css",
    ".svg": "image/svg+xml",
    ".png": "image/png",
}.items():
    mimetypes.add_type(media_type, extension)
PUBLIC_JOB_FIELDS = (
    "id",
    "prompt",
    "negative_prompt",
    "enhanced_prompt",
    "style",
    "size",
    "quality",
    "enhance",
    "reference_id",
    "model",
    "status",
    "favorite",
    "created_at",
    "updated_at",
    "finished_at",
    "asset_id",
    "error_code",
    "error_message",
    "provider_request_id",
)


def serialize_job(db, job):
    result = {name: job[name] for name in PUBLIC_JOB_FIELDS}
    result["favorite"] = bool(result["favorite"])
    result["enhance"] = bool(result["enhance"])
    result["image_url"] = f"/api/assets/{job['asset_id']}" if job["asset_id"] else None
    result["events"] = db.all(
        "SELECT id,stage,message,created_at FROM events WHERE job_id=? ORDER BY id", (job["id"],)
    )
    return result


def create_app(settings=None, *, start_worker=True, provider=None, bridge=None):
    settings = settings or Settings()
    database_url = settings.database_url.get_secret_value().strip()
    if os.environ.get("RENDER_EXTERNAL_URL") and not database_url:
        raise RuntimeError("Render deployment requires DATABASE_URL; local SQLite is not persistent")
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    outputs = settings.data_dir / "outputs"
    outputs.mkdir(exist_ok=True)
    if database_url and not settings.credential_encryption_key.get_secret_value().strip():
        raise RuntimeError("CREDENTIAL_ENCRYPTION_KEY is required for PostgreSQL deployments")
    assets = create_asset_store(settings)
    db = Database(database_url or settings.data_dir / "lumina.sqlite3")
    models = ModelService(db, settings, bridge or PiBridge(), assets)

    @asynccontextmanager
    async def lifespan(app):
        processes = []
        lock = FileLock(settings.data_dir / "worker.lock")
        instance = provider
        if db.is_postgres:
            from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver

            saver_context = AsyncPostgresSaver.from_conn_string(db.path)
        else:
            saver_context = AsyncSqliteSaver.from_conn_string(
                str(settings.data_dir / "checkpoints.sqlite3")
            )
        async with saver_context as saver:
            await saver.setup()
            try:
                if start_worker:
                    # This deployment intentionally runs one API process with an async pool.
                    lock.acquire(timeout=0)
                    instance = ModelRouter(models, settings, fallback=provider)
                    graph = build_graph(db, instance, outputs, saver, assets)
                    app.state.graph = graph
                    for _ in range(settings.worker_concurrency):
                        processes.append(
                            asyncio.create_task(
                                Worker(db, graph, outputs, instance, assets).run()
                            )
                        )
                app.state.provider_ready = bool(settings.ready or provider)
                yield
            finally:
                for process in processes:
                    process.cancel()
                await asyncio.gather(*processes, return_exceptions=True)
                if instance and hasattr(instance, "close"):
                    await instance.close()
                await models.close()
                if hasattr(assets, "close"):
                    assets.close()
                if lock.is_locked:
                    lock.release()

    app = FastAPI(
        title="Lumina Studio API",
        version="1.0.0",
        lifespan=lifespan,
        docs_url="/api/docs",
        openapi_url="/api/openapi.json",
        redoc_url=None,
    )
    app.state.db = db
    app.state.settings = settings
    app.state.models = models
    app.state.assets = assets
    app.state.provider_ready = bool(settings.ready or provider)
    app.add_middleware(BodyLimitMiddleware)
    app.include_router(model_routes)

    @app.middleware("http")
    async def boundaries(request, call_next):
        request_id = str(uuid.uuid4())
        origin = request.headers.get("origin")
        if request.method in {"POST", "PUT", "PATCH", "DELETE"}:
            if origin and origin.rstrip("/") not in settings.origins:
                return JSONResponse({"detail": "请求来源未获允许"}, 403)
            if request.headers.get("X-Lumina-Client") != "web":
                return JSONResponse({"detail": "缺少请求校验标记"}, 403)
        response = await call_next(request)
        response.headers["X-Request-ID"] = request_id
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "same-origin"
        response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
        if request.url.path.startswith("/api"):
            response.headers["Cache-Control"] = "no-store"
        else:
            response.headers["Content-Security-Policy"] = (
                "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' blob: data:; font-src 'self'; connect-src 'self'; object-src 'none'; base-uri 'self'; frame-ancestors 'none'"
            )
        logger.info(
            "request_id=%s method=%s path=%s status=%s",
            request_id,
            request.method,
            request.url.path,
            response.status_code,
        )
        return response

    @app.exception_handler(RequestValidationError)
    async def invalid_input(request, exc):
        # Pydantic's default error response can echo passwords and user input.
        fields = sorted({str(error["loc"][-1]) for error in exc.errors()})
        return JSONResponse({"detail": "请检查输入参数", "fields": fields}, 422)

    @app.get("/api/health")
    def health():
        db.one("SELECT 1 AS ready")
        return {
            "status": "ok",
            "provider_ready": app.state.provider_ready,
            "model": settings.image_model,
            "registration_enabled": settings.registration_enabled,
        }

    @app.post("/api/auth/register", status_code=201)
    def register(body: Registration, request: Request, response: Response):
        if not settings.registration_enabled:
            raise HTTPException(403, "当前暂不开放新账号注册")
        rate_limit(request, "register", limit=settings.registration_limit, seconds=3600)
        user_id = str(uuid.uuid4())
        password_hash = PASSWORDS.hash(body.password)
        try:
            db.execute(
                "INSERT INTO users(id,email,name,password_hash,created_at) VALUES(?,?,?,?,?)",
                (user_id, str(body.email).lower(), body.name, password_hash, time.time()),
            )
        except db.integrity_errors as exc:
            raise HTTPException(409, "注册未完成，请尝试登录或更换邮箱") from exc
        csrf = issue_session(response, db, settings, user_id)
        return {
            "user": {"id": user_id, "email": str(body.email).lower(), "name": body.name},
            "csrf_token": csrf,
        }

    @app.post("/api/auth/login")
    def login(body: Credentials, request: Request, response: Response):
        rate_limit(request, "login", limit=10, seconds=60)
        row = db.one("SELECT * FROM users WHERE email=?", (str(body.email).lower(),))
        valid = PASSWORDS.verify(body.password, row["password_hash"] if row else DUMMY_HASH)
        if not valid or not row:
            raise HTTPException(401, "邮箱或密码不正确")
        csrf = issue_session(response, db, settings, row["id"])
        return {"user": {name: row[name] for name in ("id", "email", "name")}, "csrf_token": csrf}

    @app.get("/api/auth/me")
    def me(request: Request, user=Depends(current_user)):
        return {
            "user": {name: user[name] for name in ("id", "email", "name")},
            "csrf_token": request.cookies.get("lumina_csrf", ""),
            "quota": db.quota(user["id"], settings.daily_generation_limit),
        }

    @app.post("/api/auth/logout", status_code=204)
    async def logout(request: Request, user=Depends(mutation_user)):
        await models.cancel_session(digest(request.cookies.get("lumina_session", "")))
        db.execute(
            "DELETE FROM sessions WHERE token_hash=?",
            (digest(request.cookies.get("lumina_session", "")),),
        )
        response = Response(status_code=204)
        for cookie in ("lumina_session", "lumina_csrf"):
            response.delete_cookie(
                cookie, path="/", secure=settings.cookie_secure, samesite="strict"
            )
        return response

    def owned_job(job_id, user_id):
        row = db.one(
            "SELECT * FROM jobs WHERE id=? AND user_id=? AND deleted_at IS NULL",
            (str(job_id), user_id),
        )
        if not row:
            raise HTTPException(404, "创作记录不存在")
        return row

    @app.post("/api/jobs", status_code=202)
    def create_job(
        body: GenerationSpec, request: Request, response: Response, user=Depends(mutation_user)
    ):
        try:
            key = str(uuid.UUID(request.headers.get("Idempotency-Key", "")))
        except ValueError as exc:
            raise HTTPException(422, "请提供有效的 Idempotency-Key 请求编号") from exc
        rate_limit(request, "generate", limit=10, seconds=60, user_id=user["id"])
        image_model, bindings = models.route(user["id"], body, app.state.provider_ready)
        try:
            job, created = db.create_job(
                user["id"],
                body,
                key,
                image_model,
                settings.daily_generation_limit,
                settings.global_daily_generation_limit,
                bindings,
            )
        except QuotaExceeded as exc:
            raise HTTPException(429, "今天的创作额度已用完，请在额度恢复后继续") from exc
        except GlobalQuotaExceeded as exc:
            raise HTTPException(429, "工作台今日生成额度已用完，请在额度恢复后继续") from exc
        except Conflict as exc:
            raise HTTPException(409, str(exc)) from exc
        response.status_code = 202 if created else 200
        response.headers["Location"] = f"/api/jobs/{job['id']}"
        return serialize_job(db, job)

    @app.get("/api/jobs")
    def list_jobs(
        user=Depends(current_user),
        limit: int = Query(30, ge=1, le=60),
        offset: int = Query(0, ge=0, le=100000),
        favorites: bool = False,
    ):
        clause = " AND favorite=1" if favorites else ""
        rows = db.all(
            "SELECT * FROM jobs WHERE user_id=? AND deleted_at IS NULL"
            + clause
            + " ORDER BY created_at DESC LIMIT ? OFFSET ?",
            (user["id"], limit, offset),
        )
        total = db.one(
            "SELECT COUNT(*) AS total FROM jobs WHERE user_id=? AND deleted_at IS NULL" + clause,
            (user["id"],),
        )["total"]
        return {
            "items": [serialize_job(db, row) for row in rows],
            "total": total,
            "next_offset": offset + limit if offset + limit < total else None,
        }

    @app.get("/api/jobs/{job_id}")
    def get_job(job_id: uuid.UUID, user=Depends(current_user)):
        return serialize_job(db, owned_job(job_id, user["id"]))

    @app.post("/api/jobs/{job_id}/cancel", status_code=204)
    def cancel_job(job_id: uuid.UUID, user=Depends(mutation_user)):
        owned_job(job_id, user["id"])
        if not db.cancel(str(job_id), user["id"]):
            raise HTTPException(409, "任务已经开始，暂时无法取消")
        return Response(status_code=204)

    @app.patch("/api/jobs/{job_id}")
    def favorite(job_id: uuid.UUID, body: FavoriteUpdate, user=Depends(mutation_user)):
        owned_job(job_id, user["id"])
        db.execute(
            "UPDATE jobs SET favorite=?,updated_at=? WHERE id=? AND user_id=?",
            (int(body.favorite), time.time(), str(job_id), user["id"]),
        )
        return serialize_job(db, owned_job(job_id, user["id"]))

    @app.delete("/api/jobs/{job_id}", status_code=204)
    def archive_job(job_id: uuid.UUID, user=Depends(mutation_user)):
        row = owned_job(job_id, user["id"])
        if row["status"] not in TERMINAL:
            raise HTTPException(409, "请在任务完成或取消后移出作品库")
        db.execute(
            "UPDATE jobs SET deleted_at=? WHERE id=? AND user_id=?",
            (time.time(), str(job_id), user["id"]),
        )
        return Response(status_code=204)

    @app.get("/api/jobs/{job_id}/events")
    async def job_events(job_id: uuid.UUID, request: Request, user=Depends(current_user)):
        owned_job(job_id, user["id"])

        async def stream():
            last_version = None
            for _ in range(600):
                if await request.is_disconnected():
                    break
                row = db.one(
                    "SELECT * FROM jobs WHERE id=? AND user_id=?", (str(job_id), user["id"])
                )
                if not row or row["deleted_at"]:
                    break
                if row["updated_at"] != last_version:
                    payload = serialize_job(db, row)
                    yield f"event: job\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n"
                    last_version = row["updated_at"]
                else:
                    yield ": heartbeat\n\n"
                if row["status"] in TERMINAL:
                    break
                await asyncio.sleep(1)

        return StreamingResponse(
            stream(),
            media_type="text/event-stream",
            headers={"X-Accel-Buffering": "no", "Cache-Control": "no-cache"},
        )

    @app.post("/api/assets", status_code=201)
    async def upload(request: Request, user=Depends(mutation_user)):
        rate_limit(request, "upload", limit=20, seconds=60, user_id=user["id"])
        if request.headers.get("content-type", "").split(";")[0] not in {
            "image/png",
            "image/jpeg",
            "image/webp",
        }:
            raise HTTPException(415, "请上传 PNG、JPG 或 WebP 图片")
        body = bytearray()
        async for chunk in request.stream():
            if len(body) + len(chunk) > 8 * 1024 * 1024:
                raise HTTPException(413, "图片不能超过 8 MB")
            body.extend(chunk)
        try:
            content, width, height = await asyncio.to_thread(normalize_image, bytes(body))
        except ValueError as exc:
            raise HTTPException(422, "图片无法读取，或尺寸超过 4096 像素") from exc
        asset_id = str(uuid.uuid4())
        path = await asyncio.to_thread(assets.upload, user["id"], asset_id, content)
        try:
            db.execute(
                "INSERT INTO assets(id,user_id,path,width,height,created_at) VALUES(?,?,?,?,?,?)",
                (asset_id, user["id"], str(path), width, height, time.time()),
            )
        except BaseException:
            await asyncio.to_thread(assets.delete, path)
            raise
        return {
            "id": asset_id,
            "image_url": f"/api/assets/{asset_id}",
            "width": width,
            "height": height,
        }

    @app.get("/api/assets/{asset_id}")
    def get_asset(asset_id: uuid.UUID, download: bool = False, user=Depends(current_user)):
        asset = db.one(
            "SELECT a.* FROM assets a LEFT JOIN jobs j ON j.id=a.job_id WHERE a.id=? AND a.user_id=? AND (a.job_id IS NULL OR j.deleted_at IS NULL)",
            (str(asset_id), user["id"]),
        )
        if not asset or not assets.exists(asset["path"]):
            raise HTTPException(404, "图片不存在")
        content = assets.read(asset["path"])
        disposition = "attachment" if download else "inline"
        return Response(
            content,
            media_type="image/png",
            headers={
                "Content-Disposition": f'{disposition}; filename="lumina-{asset_id}.png"',
                "Cache-Control": "private, no-store",
                "X-Content-Type-Options": "nosniff",
            },
        )

    dist = ROOT / "dist"
    if (dist / "assets").exists():
        app.mount("/assets", StaticFiles(directory=dist / "assets"), name="frontend-assets")

    @app.get("/{path:path}", include_in_schema=False)
    def frontend(path: str):
        if path.startswith("api/"):
            raise HTTPException(404, "接口不存在")
        if path == "favicon.svg" and (dist / path).is_file():
            return FileResponse(dist / path)
        if path.startswith("examples/"):
            candidate = (dist / path).resolve()
            if candidate.is_relative_to(dist.resolve()) and candidate.is_file():
                return FileResponse(candidate)
            raise HTTPException(404)
        if not (dist / "index.html").is_file():
            raise HTTPException(503, "请先运行 npm run build，或打开前端开发地址")
        return FileResponse(dist / "index.html")

    return app


app = create_app()
