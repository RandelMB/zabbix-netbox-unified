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
