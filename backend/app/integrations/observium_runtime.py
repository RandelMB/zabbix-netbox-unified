from __future__ import annotations

from typing import Any

import docker
import pymysql
from docker.errors import DockerException
from fastapi import HTTPException

from app.shared.security import redact_command_args


class ObserviumRuntime:
    def __init__(self, *, db_config: dict[str, Any], container_name: str, logger: Any) -> None:
        self.db_config = db_config
        self.container_name = container_name
        self.logger = logger

    def db_query(self, query: str, params: tuple[Any, ...] = (), fetch: str = "all") -> Any:
        if not self.db_config.get("database") or not self.db_config.get("user") or not self.db_config.get("password"):
            raise HTTPException(400, "Observium DB is not configured")
        connection = pymysql.connect(**self.db_config)
        try:
            with connection.cursor() as cursor:
                cursor.execute(query, params)
                if fetch == "one":
                    return cursor.fetchone()
                if fetch == "none":
                    return None
                return cursor.fetchall()
        finally:
            connection.close()

    def _docker_client(self):
        try:
            return docker.from_env()
        except DockerException as exc:
            raise HTTPException(500, f"Docker client unavailable: {exc}") from exc

    def _container(self):
        try:
            return self._docker_client().containers.get(self.container_name)
        except DockerException as exc:
            raise HTTPException(500, f"Observium container unavailable: {exc}") from exc

    def exec(self, args: list[str], *, fail_on_error: bool = True) -> dict[str, Any]:
        safe_args = redact_command_args(args)
        self.logger.info("Observium exec: %s", " ".join(safe_args))
        result = self._container().exec_run(args)
        output = result.output.decode("utf-8", errors="replace")
        if fail_on_error and result.exit_code != 0:
            raise HTTPException(400, f"Observium command failed: {output}")
        return {"command": safe_args, "output": output, "exit_code": int(result.exit_code)}
