# ZNEditor — Zabbix ↔ NetBox ↔ Observium Manual Editor

Editor visual avanzado para gestión manual, correlación y sincronización controlada entre Zabbix, NetBox y Observium.

## Inicio rápido

```bash
# 1. Clonar y configurar
cp .env.example .env
# Editar .env con tus URLs y tokens

# 2. Levantar
docker compose up --build

# 3. Acceder
# Frontend: http://localhost:3000
# Backend API docs: http://localhost:8000/docs
```

## Estructura del proyecto

```
zabbix-netbox-editor/
├── backend/
│   ├── main.py              # FastAPI — composición y rutas heredadas
│   ├── app/                 # Nueva base modular del backend
│   │   ├── core/            # app, settings y bootstrap
│   │   ├── repositories/    # persistencia local
│   │   ├── services/        # utilidades y servicios compartidos
│   │   ├── integrations/    # adapters externos
│   │   ├── routes/          # routers por dominio
│   │   ├── schemas/         # modelos y DTOs
│   │   ├── domain/          # reglas de negocio
│   │   └── shared/          # helpers transversales
│   ├── tests/               # pruebas unitarias del backend
│   ├── requirements.txt
│   └── Dockerfile
├── .speckit/
│   ├── constitution.md      # Reglas globales obligatorias
│   ├── architecture.md      # Arquitectura actual y objetivo
│   ├── integrations.md      # Contratos y modos de integración
│   ├── specs/               # Specs por feature
│   └── templates/           # Plantillas reutilizables
├── frontend/
│   ├── src/
│   │   ├── App.js           # Navegación principal
│   │   ├── pages/
│   │   │   ├── DeviceListPage.js   # Inventory: lista dual Z + NB
│   │   │   ├── WorkspacePage.js    # Editor multi-pestaña
│   │   │   └── SettingsPage.js     # Credenciales + mapeo
│   │   ├── components/
│   │   │   ├── ZabbixEditor.js     # Editor completo de host Zabbix
│   │   │   ├── NetBoxEditor.js     # Editor completo de dispositivo NetBox
│   │   │   ├── TransferPanel.js    # Transferencia bidireccional Z⇄NB
│   │   │   ├── ConfirmModal.js     # Preview JSON + confirmación
│   │   │   └── LogPanel.js         # Log de operaciones en tiempo real
│   │   └── utils/api.js     # Cliente HTTP para backend
│   ├── nginx.conf
│   └── Dockerfile
├── docker-compose.yml
└── .env.example
```

## SpecKit

El proyecto incluye un SpecKit completo en `.speckit/` para formalizar:

- reglas globales de arquitectura y calidad
- integraciones con Zabbix, NetBox, Observium y SNMP
- contratos, flows, edge cases y tareas por feature
- estrategia de migración para sacar lógica de `backend/main.py`
- transición incremental hacia `backend/app/` sin romper los endpoints actuales

Uso recomendado:

1. leer `.speckit/constitution.md` antes de cambios grandes
2. revisar `.speckit/architecture.md` y `.speckit/integrations.md`
3. actualizar el spec de la feature correspondiente antes de refactor o expansión

## Contexto operativo

El proyecto no usa el historial del chat como contexto persistente.

Fuente oficial:

- `.speckit/contexto.txt`

Reglas:

- se lee antes de cambios relevantes, agentes o generación de código
- describe solo el estado actual del sistema
- no funciona como bitácora acumulativa
- se actualiza reemplazando información obsoleta
- si una conversación contradice `.speckit/contexto.txt`, prevalece `.speckit/contexto.txt`

## Estado de la reorganización

La migración del backend ya dejó `backend/main.py` como bootstrap. Actualmente:

- `backend/main.py` solo registra startup e incluye routers
- `backend/app/core/` centraliza `app`, logging y configuración
- `backend/app/repositories/app_db.py` centraliza SQLite local
- `backend/app/repositories/archive_repository.py` encapsula archivado y export logs
- `backend/app/repositories/correlations_repository.py` encapsula correlaciones persistentes
- `backend/app/repositories/sync_profiles_repository.py` encapsula perfiles de sync NetBox
- `backend/app/routes/settings.py` encapsula credenciales runtime, checks y `/api/settings`
- `backend/app/routes/archive.py` encapsula archivado lógico
- `backend/app/routes/correlations.py` encapsula correlaciones
- `backend/app/routes/topology.py` encapsula `inventory.drawio`
- `backend/app/routes/zabbix.py` encapsula endpoints Zabbix
- `backend/app/routes/netbox.py` encapsula endpoints NetBox, sync, SNMP y enrichment
- `backend/app/routes/discovery.py` encapsula LLDP discovery
- `backend/app/routes/observium.py` encapsula CRUD Observium y export manual
- `backend/app/services/sync_locks.py` centraliza los locks de sync
- `backend/app/services/interface_sync.py` concentra preview/apply de sync de interfaces
- `backend/app/services/legacy_runtime.py` contiene el runtime heredado extraído de `main.py` y consumido desde routers dedicados
- `backend/app/services/snmp_discovery.py` concentra validación, probe e import SNMP
- `backend/app/services/settings_service.py` centraliza snapshot runtime y checks de integración
- `backend/app/services/topology_export_service.py` centraliza export draw.io
- `backend/app/schemas/snmp.py` define el contrato SNMP v2c/v3
- `backend/app/schemas/` ya incluye contratos para archive, correlations y settings
- `backend/app/shared/` concentra helpers puros reutilizables
- `backend/app/shared/security.py` redacciona secretos antes de logs y respuestas
- `backend/app/integrations/observium_web.py` habilita fallback de lectura por sesión web

La siguiente fase debe seguir desacoplando `backend/app/services/legacy_runtime.py` internamente por bounded context, manteniendo `main.py` como entrypoint mínimo.

## Observium

El backend soporta dos modos:

- `DB/CLI`: modo preferente cuando existe conectividad a la base y al contenedor de Observium
- `web_session`: fallback de lectura cuando la DB no está disponible
- `metadata-only local`: despliegue local Community con discovery dirigido y poller deshabilitado por configuración

Variables relevantes:

- `OBSERVIUM_BASE_URL`
- `OBSERVIUM_WEB_USER`
- `OBSERVIUM_WEB_PASS`
- `OBSERVIUM_WEB_PASS_B64`
- `OBSERVIUM_DB_HOST`
- `OBSERVIUM_DB_PORT`
- `OBSERVIUM_DB_NAME`
- `OBSERVIUM_DB_USER`
- `OBSERVIUM_DB_PASSWORD`
- `OBSERVIUM_ENABLE_POLLER`
- `OBSERVIUM_DISCOVERY_MODULES`

Notas:

- `OBSERVIUM_WEB_PASS_B64` evita problemas de interpolación de `$` en Docker Compose
- el fallback web cubre lectura de dispositivos, puertos y vecinos para preview/sync
- la escritura directa hacia Observium sigue dependiendo del stack local de Observium
- `deploy/observium-local/` contiene un stack local de soporte con `poller.php` deshabilitado y discovery solo manual/dirigido
- en Observium Community la API REST nativa de Subscription no está presente; la integración operativa sigue siendo por DB/CLI/Web

## Pruebas

Backend:

```bash
docker exec zneditor-backend python -m unittest discover -s /app/tests -v
docker exec zneditor-backend python -m compileall /app
```

Frontend:

```bash
cd frontend
npm run build
```

## Flujo de uso

### 1. Configurar credenciales
- Ir a **Settings**
- Ingresar URL y token/credenciales de Zabbix y NetBox
- Hacer clic en "Test Connections" para verificar

### 2. Cargar inventario
- Ir a **Inventory**
- Clic en "↺ Load All" (o botones individuales Z / NB)
- Ver lista completa de hosts Zabbix y dispositivos NetBox

### 3. Editar un dispositivo
- Clic en **Open →** en cualquier fila
- Se abre en el **Workspace** como pestaña
- Editar en tabs: General / Ports / Sync / Raw JSON
- **General** concentra descripción y comments
- **Ports** unifica:
  - asignación de IP principal a `VLAN 200` o a la interfaz de gestión elegida
  - edición manual de puertos (`name`, `type`, `enabled`, `mac`, `description`, `ip`)
  - sincronización completa de puertos contra Observium

### 4. Abrir múltiples en paralelo
- Volver a Inventory y abrir otro dispositivo
- Se crea nueva pestaña en Workspace
- Editables en paralelo sin interferencia

### 5. Transferir datos entre sistemas
- Tener abierto al menos 1 host Zabbix y 1 dispositivo NetBox
- En Workspace → botón **⇄ Transfer**
- Seleccionar dirección: Z → NB o NB → Z
- Clic en "Build Payload Preview"
- Revisar JSON → "Preview & Execute Transfer"
- Confirmar en modal con JSON editable

### 6. Sincronizar campos del equipo
- Abrir un dispositivo NetBox con correlación guardada
- Ir a **Workspace → NetBox → Sync**
- Elegir origen por campo para:
  - `primary_ip4`
  - `serial`
  - `platform`
- Guardar perfil o ejecutar **Preview & Run Sync**

### 7. Sincronizar puertos NetBox ↔ Observium
- Abrir un dispositivo NetBox con correlación guardada hacia Observium
- Ir a **Workspace → NetBox → Ports**
- Usar **Refresh Interface Preview** dentro de **Observium Port Sync**
- Revisar propuestas de:
  - renombre de interfaz usando el nombre corto real de Observium (`port1`, `gi1/0`, `eth 1`, etc.)
  - descripción
  - MAC address
  - estado administrativo
  - tipo de interfaz
  - MTU
  - tags de VLAN, con visualización compacta por rangos cuando aplica (`1-1024`) y lista corta cuando son pocas VLANs
- membresía LAG/LACP cuando Observium la expone
- cables/conexiones cuando el vecino remoto ya puede resolverse en NetBox
- creación directa de interfaces `unmatched_observium`
- La ejecución del sync ahora:
  - elimina las interfaces actuales del dispositivo en NetBox
  - recrea los puertos desde Observium
  - reubica la IP principal del equipo sobre `VLAN 200` o sobre la interfaz de gestión configurada
  - serializa la ejecución por dispositivo para evitar syncs superpuestos y resultados inconsistentes
- Ajustar los checkboxes y ejecutar **Preview & Run Port Sync**
- El backend crea automáticamente tags `VLAN <id>` en NetBox cuando no existen

### 8. Discovery
- Ir a **Discovery** desde la navegación superior, al lado de **Workspace**
- En **LLDP Neighbor Discovery** elegir el `Device1` desde Observium o Zabbix
- El backend resuelve el equipo en Observium, lee vecinos LLDP/CDP, intenta resolver MAC e IP por tabla ARP y compara cada `Device2` contra NetBox, Zabbix y Observium
- Desde esa misma vista se pueden crear en NetBox solo los vecinos seleccionados
- En **Add Device to NetBox by SNMP** el probe consulta por IP y SNMP, extrae `sysName`, `sysDescr`, `sysObjectID`, vendor, modelo, serial y cruza coincidencias existentes
- El probe acepta `SNMP v2c` y `SNMP v3` (`noAuthNoPriv`, `authNoPriv`, `authPriv`) con validación cerrada antes de salir a red
- Si no existe `manufacturer` o `device_type` en NetBox, el import los crea automáticamente antes de registrar el equipo
- La descripción propuesta para NetBox usa el sistema operativo junto con su versión cuando esa información está disponible
- El flujo también se usa para altas rápidas de Access Points Aruba con IP principal, plataforma, MAC de `mgmt0`, serial y comentarios operativos

### 9. Confirmación obligatoria
- NINGÚN cambio se ejecuta sin pasar por el modal de Preview
- El JSON es editable antes de confirmar
- Todo queda registrado en el Log Panel

## API endpoints

### Zabbix
| Método | Ruta | Descripción |
|--------|------|-------------|
| GET | `/api/zabbix/hosts` | Listar todos los hosts |
| GET | `/api/zabbix/hosts/{id}` | Detalle de host |
| PATCH | `/api/zabbix/hosts/{id}` | Actualizar host |
| POST | `/api/zabbix/hosts` | Crear host |
| GET | `/api/zabbix/interfaces/{host_id}` | Interfaces de host |
| POST | `/api/zabbix/interfaces` | Crear interfaz |
| PATCH | `/api/zabbix/interfaces/{id}` | Actualizar interfaz |
| DELETE | `/api/zabbix/interfaces/{id}` | Eliminar interfaz |

### NetBox
| Método | Ruta | Descripción |
|--------|------|-------------|
| GET | `/api/netbox/devices` | Listar dispositivos |
| GET | `/api/netbox/devices/{id}` | Detalle de dispositivo |
| PATCH | `/api/netbox/devices/{id}` | Actualizar dispositivo |
| GET | `/api/netbox/devices/{id}/interfaces` | Interfaces |
| POST/PATCH/DELETE | `/api/netbox/interfaces/...` | CRUD interfaces |
| GET/POST/PATCH/DELETE | `/api/netbox/ips/...` | CRUD IPs |
| GET | `/api/netbox/devices/{id}/sync` | Preview del sync manual por campos |
| POST | `/api/netbox/devices/{id}/sync/run` | Ejecutar sync manual por campos |
| GET | `/api/netbox/devices/{id}/interface-sync` | Preview manual de puertos contra Observium |
| POST | `/api/netbox/devices/{id}/interface-sync/run` | Reemplazar interfaces y sincronizar puertos |
| POST | `/api/netbox/snmp-discovery/preview` | Probe SNMP por IP para alta en NetBox |
| POST | `/api/netbox/snmp-discovery/import` | Crear dispositivo NetBox a partir del probe SNMP |
| POST | `/api/discovery/lldp/preview` | Descubrir vecinos LLDP/CDP desde un equipo origen en Observium o Zabbix |
| POST | `/api/discovery/lldp/apply` | Crear en NetBox los vecinos seleccionados desde Discovery |

### Configuración
| Método | Ruta | Descripción |
|--------|------|-------------|
| GET/POST | `/api/credentials` | Leer/guardar credenciales runtime |
| GET | `/api/settings` | Snapshot enmascarado de settings, TLS y provider |
| GET | `/api/check/zabbix` | Test conexión Zabbix |
| GET | `/api/check/netbox` | Test conexión NetBox |
| GET | `/api/check/observium` | Test conexión Observium |
| GET/PUT | `/api/mapping` | Leer/guardar mapeo de campos |

## Variables de entorno

| Variable | Descripción |
|----------|-------------|
| `SECRET_PROVIDER` | Proveedor de secretos: `environment`, `vault`, `custom` |
| `CUSTOM_SECRET_PROVIDER_CLASS` | Clase Python para proveedor custom (`modulo.Clase` o `modulo:Clase`) |
| `VAULT_ADDR` | URL base de HashiCorp Vault |
| `VAULT_TOKEN` | Token de acceso de Vault |
| `VAULT_KV_MOUNT` | Mount KV de Vault, por ejemplo `secret` |
| `VAULT_KV_PATH_PREFIX` | Prefijo lógico del proyecto dentro de Vault |
| `VAULT_KV_VERSION` | Versión KV soportada: `v1` o `v2` |
| `ZABBIX_URL` | URL base de Zabbix (sin `/`) |
| `ZABBIX_TOKEN` | API Token de Zabbix (Zabbix 5.4+) |
| `ZABBIX_USER` | Usuario (fallback si no hay token) |
| `ZABBIX_PASS` | Contraseña (fallback) |
| `NETBOX_URL` | URL base de NetBox |
| `NETBOX_TOKEN` | Token de NetBox |
| `OBSERVIUM_WEB_PASS_B64` | Password web de Observium en base64 para evitar problemas de interpolación |
| `OUTBOUND_TLS_VERIFY` | Política TLS saliente por defecto para integraciones HTTP |
| `VAULT_TLS_VERIFY` | Override de TLS para Vault |
| `ZABBIX_TLS_VERIFY` | Override de TLS para Zabbix API |
| `NETBOX_TLS_VERIFY` | Override de TLS para NetBox API |
| `OBSERVIUM_WEB_TLS_VERIFY` | Override de TLS para fallback `web_session` |
| `OBSERVIUM_ENABLE_POLLER` | Habilita `poller.php` desde backend; por defecto debe permanecer en `false` para el modo local |
| `OBSERVIUM_DISCOVERY_MODULES` | Lista CSV de módulos permitidos para discovery dirigido de metadata |

## Secrets providers

El backend resuelve secretos a traves de una capa abstracta en `backend/app/core/secret_providers.py`.

Proveedores soportados:

- `environment`: lee desde variables de entorno
- `vault`: lee desde HashiCorp Vault KV
- `custom`: carga una clase Python propia

La configuracion y el codigo de dominio no deben acoplarse directamente a un proveedor concreto.

> Las credenciales también se pueden ingresar desde la UI en Settings,
> sin necesidad de reiniciar el contenedor.

## Seguridad

- Las credenciales se guardan en memoria del proceso backend (no en disco)
- Las variables `.env` son el fallback de inicio
- La UI puede sobreescribir credenciales en runtime via `/api/credentials`
- Usar HTTPS en producción (configurar en nginx o via proxy externo)
- TLS saliente se valida por defecto; solo deshabilitarlo con un flag `*_TLS_VERIFY=false` cuando el entorno local lo requiera explícitamente
- El backend redacciona secretos SNMP, cookies y tokens antes de registrarlos o devolverlos en previews
