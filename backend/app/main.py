import logging
import time
from collections import OrderedDict, deque
from contextlib import asynccontextmanager
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.trustedhost import TrustedHostMiddleware
from fastapi.responses import JSONResponse
from app.config import settings
from app.database import engine
from app.routers import assistant, auth, catalog, environment, projects, runs, runner
from app.utils.logging import configure_logging

configure_logging(settings.log_level)
logger=logging.getLogger("pocketlab.api")

@asynccontextmanager
async def lifespan(app:FastAPI):
    logger.info("application_start",extra={"environment":settings.environment})
    yield
    engine.dispose()
    logger.info("application_shutdown")

app=FastAPI(title="PocketLab API",version="1.0.0",description="Backend for constrained Android application testing.",lifespan=lifespan)
app.add_middleware(TrustedHostMiddleware,allowed_hosts=settings.trusted_hosts)

# Small per-process sliding window limiter. Bound client state so requests from
# many distinct addresses cannot grow this process-local map without limit.
# Production deployments behind multiple workers should use a shared gateway/Redis limiter.
RATE_LIMIT_MAX_CLIENTS = 10_000
_requests: OrderedDict[str, deque[float]] = OrderedDict()
@app.middleware("http")
async def access_log_and_rate_limit(request:Request,call_next):
    client=request.client.host if request.client else "unknown"
    # The execution worker (separately token-authenticated) gets its own bucket so dashboard polling and a
    # worker on the same machine cannot starve each other.
    is_runner=request.url.path.startswith("/api/v1/runner/")
    now=time.monotonic(); key=("runner:" if is_runner else "")+client
    window=_requests.get(key)
    if window is None:
        if len(_requests) >= RATE_LIMIT_MAX_CLIENTS:
            _requests.popitem(last=False)
        window=deque()
        _requests[key]=window
    else:
        _requests.move_to_end(key)
    while window and window[0] <= now-60:window.popleft()
    try:limit=int(settings.rate_limit.split("/")[0])
    except ValueError:limit=120
    if is_runner:limit*=5
    if len(window)>=limit:return JSONResponse(status_code=429,content={"detail":"Rate limit exceeded"})
    if request.method=="POST" and request.url.path.startswith("/api/v1/projects/") and request.url.path.endswith("/builds"):
        content_length=request.headers.get("content-length")
        if content_length and content_length.isdigit() and int(content_length)>settings.max_apk_size_bytes+1024*1024:
            return JSONResponse(status_code=413,content={"detail":"APK request exceeds configured size limit"})
    window.append(now);started=time.monotonic()
    try:response=await call_next(request)
    except Exception:
        # Answer with JSON instead of re-raising: a re-raised error is turned into a 500 outside CORSMiddleware, the
        # browser then hides it and the dashboard can only say the API is unreachable.
        logger.exception("request_failed",extra={"path":request.url.path,"method":request.method})
        response=JSONResponse(status_code=500,content={"detail":"Internal server error"})
    logger.info("http_request",extra={"path":request.url.path,"method":request.method,"status_code":response.status_code,"duration_ms":round((time.monotonic()-started)*1000,2)})
    return response

# Added last so it is the outermost middleware: every response, including the 429/413 answers above, carries CORS
# headers and the dashboard can show the real error instead of "Could not reach the PocketLab API".
app.add_middleware(CORSMiddleware,allow_origins=settings.cors_origins,allow_origin_regex=settings.cors_origin_regex,allow_credentials=True,allow_methods=["GET","POST","PUT","DELETE","OPTIONS"],allow_headers=["Authorization","Content-Type","Idempotency-Key"],expose_headers=["Content-Disposition"])

for router in (auth.router,projects.router,catalog.router,runs.router,runner.router,environment.router,assistant.router):app.include_router(router)

@app.get("/health/live",tags=["health"])
def live():return {"status":"ok"}

@app.get("/health/ready",tags=["health"])
def ready():
    from sqlalchemy import text
    from sqlalchemy.exc import SQLAlchemyError
    try:
        with engine.connect() as conn:conn.execute(text("SELECT 1"))
        return {"status":"ok","database":"ok"}
    except SQLAlchemyError:
        return JSONResponse(status_code=503,content={"status":"error","database":"unavailable"})
