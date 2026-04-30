from __future__ import annotations
import logging
import asyncio
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from pathlib import Path

from .models import (
    SearchRequest, ComparisonResult,
    ConfirmPurchaseRequest,
    CredentialRequest, CredentialStatus,
    LoginRequest, LoginResult,
    HealthResponse, HistoryStats,
)
from .services import comparison as comparison_service
from .services import history_service
from .services.bulk_advisor import attach_bulk_suggestions
from .services.credential_service import save_credentials, credentials_exist
from .stores.dirk import close_browser as close_dirk_browser
from .stores.albert_heijn import close_ah_browser
from .stores.jumbo import close_jumbo_browser
from .stores.lidl import close_browser as close_lidl_browser
from .stores.albert_heijn import AHClient
from .stores.jumbo import JumboClient
from .stores.dirk import DirkClient
from .stores.lidl import LidlClient
from .database import init_db
from . import config

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

FRONTEND_DIR = Path(__file__).parent.parent / "public"


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    config.ensure_storage_dir()
    logger.info("Database initialized at %s", config.DB_PATH)
    yield
    await close_dirk_browser()
    await close_ah_browser()
    await close_jumbo_browser()
    await close_lidl_browser()
    logger.info("Playwright browsers closed")


app = FastAPI(title="Supermarkt Vergelijker", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*", "null"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ── Search ──────────────────────────────────────────────────────────────────

@app.post("/api/search", response_model=ComparisonResult)
async def search(request: SearchRequest):
    if not request.items:
        raise HTTPException(status_code=400, detail="No items provided")

    result = await comparison_service.compare(request.items, request.stores)

    # Build raw store results map for bulk advisor
    # Re-use the search results already computed inside comparison_service
    # by attaching bulk suggestions based on best_per_store data per item
    _attach_bulk(result)

    return result


def _attach_bulk(result: ComparisonResult):
    """Attach bulk suggestions using per-store product data already in item results."""
    for ir in result.items:
        all_products = [p for p in ir.best_per_store.values() if p is not None]
        if len(all_products) >= 2:
            from .services.bulk_advisor import _find_bulk_suggestions
            suggestion = _find_bulk_suggestions(all_products)
            if suggestion:
                ir.bulk_suggestion = suggestion


# ── Purchase History ─────────────────────────────────────────────────────────

@app.post("/api/confirm-purchase")
async def confirm_purchase(request: ConfirmPurchaseRequest):
    try:
        history_service.record_purchase(request.store, request.items, request.quantities)
        return {"saved": len(request.items)}
    except Exception as e:
        logger.error("Failed to save purchase: %s", e)
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/history")
async def get_history(
    limit: int = Query(50, ge=1, le=500),
    store: str | None = Query(None),
    product_name: str | None = Query(None),
):
    records = history_service.get_history(limit=limit, store=store, product_name=product_name)
    return {"records": [r.model_dump() for r in records]}


@app.get("/api/history/stats", response_model=HistoryStats)
async def get_history_stats():
    return history_service.get_stats()


# ── Credentials ──────────────────────────────────────────────────────────────

@app.post("/api/credentials")
async def store_credentials(request: CredentialRequest):
    try:
        save_credentials(request.store, request.username, request.password)
        return {"stored": True}
    except Exception as e:
        logger.error("Failed to save credentials: %s", e)
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/credentials/status", response_model=CredentialStatus)
async def credentials_status():
    return CredentialStatus(
        ah=credentials_exist("ah"),
        jumbo=credentials_exist("jumbo"),
    )


# ── Auth ─────────────────────────────────────────────────────────────────────

@app.post("/api/auth/login", response_model=LoginResult)
async def login(request: LoginRequest):
    if request.store == "ah":
        from .auth.ah_auth import login as ah_login
        success, method = await ah_login()
    else:
        from .auth.jumbo_auth import login as jumbo_login
        success, method = await jumbo_login()

    return LoginResult(
        success=success,
        method=method,
        message="Logged in successfully" if success else f"Login failed ({method})",
    )


# ── Health ───────────────────────────────────────────────────────────────────

@app.get("/api/health", response_model=HealthResponse)
async def health():
    clients = {"ah": AHClient(), "jumbo": JumboClient(), "dirk": DirkClient(), "lidl": LidlClient()}
    reachability = await asyncio.gather(*[c.is_reachable() for c in clients.values()])
    stores_reachable = dict(zip(clients.keys(), reachability))

    return HealthResponse(
        status="ok",
        stores_reachable=stores_reachable,
        authenticated={
            "ah": config.get_token("ah") is not None,
            "jumbo": config.get_token("jumbo") is not None,
        },
    )


# ── Frontend static files ─────────────────────────────────────────────────────

if FRONTEND_DIR.exists():
    @app.get("/")
    async def serve_frontend():
        return FileResponse(str(FRONTEND_DIR / "index.html"))

    @app.get("/style.css")
    async def serve_css():
        return FileResponse(str(FRONTEND_DIR / "style.css"))

    @app.get("/app.js")
    async def serve_js():
        return FileResponse(str(FRONTEND_DIR / "app.js"))

    logos_dir = FRONTEND_DIR / "logos"
    if logos_dir.exists():
        app.mount("/logos", StaticFiles(directory=str(logos_dir)), name="logos")
