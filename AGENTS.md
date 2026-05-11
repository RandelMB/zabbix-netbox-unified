# AGENTS.md

## Fuente oficial de contexto

- Leer siempre `.speckit/contexto.txt` antes de cambios relevantes.
- El historial del chat no es contexto persistente del proyecto.
- Si el chat contradice `.speckit/contexto.txt`, prevalece `.speckit/contexto.txt`.

## Reglas obligatorias

- Seguir `.speckit/constitution.md`, `.speckit/architecture.md`, `.speckit/standards.md`, `.speckit/security.md`, `.speckit/integrations.md`, `.speckit/testing.md` y `.speckit/anti-patterns.md`.
- Toda feature o refactor importante debe reflejarse en `.speckit/specs/`.
- `backend/main.py` debe actuar como composición/compatibilidad temporal, no como centro permanente de lógica.
- No exponer secretos en logs, previews, errores, ejemplos ni persistencia local.
- No mezclar parseo HTML, llamadas externas o SQL inline dentro de endpoints.
- Todo sync destructivo requiere `preview`, `apply`, `verify` y recuperación documentada.

## Entrega

- Actualizar `README.md` y `.speckit/contexto.txt` al cerrar cambios.
- Mantener el contexto compacto, técnico y centrado en el estado actual.
