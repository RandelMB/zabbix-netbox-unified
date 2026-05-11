from __future__ import annotations

from fastapi import APIRouter

from app.core.settings import cfg


router = APIRouter()


@router.get("/api/health")
async def health():
    return {"status": "ok", "version": "1.3.0", "db": cfg.APP_DB_PATH}
