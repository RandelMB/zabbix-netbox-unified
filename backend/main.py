from app.core.application import app
from app.repositories.app_db import init_app_db
from app.routes.archive import router as archive_router
from app.routes.correlations import router as correlations_router
from app.routes.discovery import router as discovery_router
from app.routes.health import router as health_router
from app.routes.netbox import router as netbox_router
from app.routes.observium import router as observium_router
from app.routes.settings import router as settings_router
from app.routes.topology import router as topology_router
from app.routes.zabbix import router as zabbix_router


@app.on_event("startup")
def on_startup() -> None:
    init_app_db()


for router in (
    settings_router,
    archive_router,
    correlations_router,
    topology_router,
    health_router,
    zabbix_router,
    netbox_router,
    discovery_router,
    observium_router,
):
    app.include_router(router)
