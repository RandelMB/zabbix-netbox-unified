# ZNEditor — Zabbix ↔ NetBox Manual Editor

Editor visual avanzado para gestión manual y transferencia de datos entre Zabbix y NetBox.

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
│   ├── main.py              # FastAPI — todos los endpoints
│   ├── requirements.txt
│   └── Dockerfile
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
- Editar en tabs: General / Interfaces / IPs / Inventory / etc.

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

### 6. Confirmación obligatoria
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

### Configuración
| Método | Ruta | Descripción |
|--------|------|-------------|
| GET/POST | `/api/credentials` | Leer/guardar credenciales |
| GET | `/api/check/zabbix` | Test conexión Zabbix |
| GET | `/api/check/netbox` | Test conexión NetBox |
| GET/PUT | `/api/mapping` | Leer/guardar mapeo de campos |

## Variables de entorno

| Variable | Descripción |
|----------|-------------|
| `ZABBIX_URL` | URL base de Zabbix (sin `/`) |
| `ZABBIX_TOKEN` | API Token de Zabbix (Zabbix 5.4+) |
| `ZABBIX_USER` | Usuario (fallback si no hay token) |
| `ZABBIX_PASS` | Contraseña (fallback) |
| `NETBOX_URL` | URL base de NetBox |
| `NETBOX_TOKEN` | Token de NetBox |

> Las credenciales también se pueden ingresar desde la UI en Settings,
> sin necesidad de reiniciar el contenedor.

## Seguridad

- Las credenciales se guardan en memoria del proceso backend (no en disco)
- Las variables `.env` son el fallback de inicio
- La UI puede sobreescribir credenciales en runtime via `/api/credentials`
- Usar HTTPS en producción (configurar en nginx o via proxy externo)
