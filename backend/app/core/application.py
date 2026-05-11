import logging

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.shared.observability import RequestContextMiddleware

app = FastAPI(title="Zabbix-NetBox-Observium Editor", version="1.3.0")
logger = logging.getLogger("uvicorn.error")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.add_middleware(RequestContextMiddleware)
