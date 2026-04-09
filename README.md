# ZNEditor

<p align="center">
  <strong>Visual workspace for Zabbix, NetBox and Observium</strong><br>
  Manual editing, correlation management, NetBox sync, Observium export and inventory review from one interface.
</p>

<p align="center">
  <img src="https://img.shields.io/badge/FastAPI-backend-009688?style=for-the-badge" alt="FastAPI">
  <img src="https://img.shields.io/badge/React-frontend-61dafb?style=for-the-badge" alt="React">
  <img src="https://img.shields.io/badge/Docker-compose-2496ed?style=for-the-badge" alt="Docker">
  <img src="https://img.shields.io/badge/NetBox-DCIM%20%2F%20IPAM-2dd4ff?style=for-the-badge" alt="NetBox">
  <img src="https://img.shields.io/badge/Zabbix-monitoring-d40000?style=for-the-badge" alt="Zabbix">
  <img src="https://img.shields.io/badge/Observium-discovery-ffac30?style=for-the-badge" alt="Observium">
</p>

---

## Overview

ZNEditor is a web application built to operate across three inventory and monitoring platforms:

- `Zabbix`
- `NetBox`
- `Observium`

The app is centered around two main workflows:

1. `Inventory`: load, compare, filter and open records from all platforms.
2. `Workspace`: edit records side by side, link related devices, enrich NetBox, and export Zabbix hosts into Observium.

---

## Current Capabilities

### Inventory

- Load devices from `Zabbix`, `NetBox` and `Observium`
- Filter each source independently
- Toggle archive views: `Active`, `Archived`, `All`
- Open any device directly into `Workspace`
- Open device names directly in their native platform UI
- Show saved correlations and automatic IP-based matches
- Export selected Zabbix hosts to Observium from the inventory table
- Download `inventory.drawio` directly from the web UI

### Workspace

- Separate columns for `Zabbix`, `NetBox` and `Observium`
- Independent tabs per platform
- Quick create with `+ New` in each panel
- Side-by-side editing without leaving the page
- Central correlation panel to link or unlink loaded records
- Live operation log

### Zabbix

- Load and edit hosts
- Create SNMP hosts
- Edit interfaces
- Edit host groups
- Resolve SNMP communities from host macros and global macros
- Open host search in the Zabbix Monitoring UI from Inventory

### NetBox

- Create and edit devices
- Edit interfaces
- Create, assign and reuse IP addresses
- Automatically set `primary_ip4` or `primary_ip6` when assigning an IP
- Sync selected fields from correlated Zabbix or Observium records
- Current sync fields:
  - `primary_ip4`
  - `serial`
  - `description` using OS version text
  - `platform`
- Auto-create missing `Platforms` in NetBox during sync
- Ignore blank `asset_tag` values correctly on update

### Observium

- Create, update, delete and refresh devices
- Manual add flow with SNMP parameters
- Support for `Skip ICMP Echo Checks`
- Export from Zabbix into Observium with SNMP validation
- Update existing Observium device if already matched by hostname or IP

### Correlations

- Save manual links across platforms
- Detect automatic matches by IP and name
- Show correlation badges in Inventory
- Use saved correlations as the source of NetBox sync

### Draw.io Export

- Generate a `.drawio` inventory diagram from NetBox devices
- Include icon image and centered device labels
- Include:
  - device name
  - primary IP without CIDR mask
  - model
  - role

---

## Screens and Flow

### 1. Settings

Use `Settings` to:

- set runtime credentials for Zabbix and NetBox
- test platform connectivity
- review export logs
- adjust visual preferences
- inspect company profile publishing rules

### 2. Inventory

Use `Inventory` to:

- load records from each platform
- compare inventory quickly
- archive or restore records locally
- open records into the workspace
- export hosts from Zabbix to Observium
- download the NetBox inventory diagram

### 3. Workspace

Use `Workspace` to:

- open one or many records side by side
- create new records from each platform tab
- save or remove correlations
- edit Zabbix, NetBox and Observium without switching tools

---

## Quick Start

```bash
# 1. Clone
git clone <your-repo-url>
cd zabbix-netbox-unified

# 2. Configure runtime credentials
cp .env.example .env

# 3. Optional: local company-specific branding and publishing rules
cp backend/config/company.example.json backend/config/company.local.json

# 4. Start the stack
docker compose up --build
```

Access points:

- Frontend: `http://localhost:8000`
- Backend health: `http://localhost:8000/api/health`

---

## Project Structure

```text
zabbix-netbox-unified/
├── backend/
│   ├── config/
│   │   ├── company.example.json
│   │   └── company.local.json
│   ├── main.py
│   ├── requirements.txt
│   ├── Dockerfile
│   └── switch.png
├── frontend/
│   ├── src/
│   │   ├── App.js
│   │   ├── components/
│   │   │   ├── ConfirmModal.js
│   │   │   ├── LogPanel.js
│   │   │   ├── NetBoxEditor.js
│   │   │   ├── ObserviumEditor.js
│   │   │   ├── TransferPanel.js
│   │   │   └── ZabbixEditor.js
│   │   ├── pages/
│   │   │   ├── DeviceListPage.js
│   │   │   ├── SettingsPage.js
│   │   │   └── WorkspacePage.js
│   │   └── utils/
│   │       └── api.js
│   ├── Dockerfile
│   └── nginx.conf
├── data/
├── docker-compose.yml
├── .env.example
└── .gitignore
```

---

## Main API Endpoints

### Company

| Method | Path | Purpose |
|---|---|---|
| `GET` | `/api/company-profile` | Read public/company profile configuration |

### Credentials and Checks

| Method | Path | Purpose |
|---|---|---|
| `POST` | `/api/credentials` | Save runtime credentials |
| `GET` | `/api/credentials` | Read masked credentials |
| `GET` | `/api/check/zabbix` | Test Zabbix connection |
| `GET` | `/api/check/netbox` | Test NetBox connection |
| `GET` | `/api/check/observium` | Test Observium DB and container access |

### Archive and Logs

| Method | Path | Purpose |
|---|---|---|
| `GET` | `/api/archive/{source}` | List archived items |
| `POST` | `/api/archive/{source}/{external_id}` | Archive item |
| `DELETE` | `/api/archive/{source}/{external_id}` | Restore item |
| `POST` | `/api/archive/bulk` | Bulk archive/restore |
| `GET` | `/api/export-logs` | Read export execution logs |

### Correlations

| Method | Path | Purpose |
|---|---|---|
| `GET` | `/api/correlations` | List saved correlation groups |
| `GET` | `/api/correlations/match` | Match a record against saved correlation groups |
| `POST` | `/api/correlations/link` | Create or update a correlation |
| `DELETE` | `/api/correlations/{group_id}/{source}` | Unlink one source from a correlation |

### Zabbix

| Method | Path | Purpose |
|---|---|---|
| `GET` | `/api/zabbix/hosts` | List hosts |
| `GET` | `/api/zabbix/hosts/{host_id}` | Host detail |
| `POST` | `/api/zabbix/hosts` | Create host |
| `PATCH` | `/api/zabbix/hosts/{host_id}` | Update host |
| `GET` | `/api/zabbix/interfaces/{host_id}` | Host interfaces |
| `POST` | `/api/zabbix/interfaces` | Create interface |
| `PATCH` | `/api/zabbix/interfaces/{iface_id}` | Update interface |
| `DELETE` | `/api/zabbix/interfaces/{iface_id}` | Delete interface |
| `GET` | `/api/zabbix/groups` | List host groups |

### NetBox

| Method | Path | Purpose |
|---|---|---|
| `GET` | `/api/netbox/devices` | List devices |
| `GET` | `/api/netbox/devices/{device_id}` | Device detail |
| `POST` | `/api/netbox/devices` | Create device |
| `PATCH` | `/api/netbox/devices/{device_id}` | Update device |
| `POST` | `/api/netbox/devices/{device_id}/primary-ip` | Set primary IP |
| `GET` | `/api/netbox/devices/{device_id}/interfaces` | List interfaces |
| `GET` | `/api/netbox/interfaces/{iface_id}` | Interface detail |
| `POST` | `/api/netbox/interfaces` | Create interface |
| `PATCH` | `/api/netbox/interfaces/{iface_id}` | Update interface |
| `DELETE` | `/api/netbox/interfaces/{iface_id}` | Delete interface |
| `GET` | `/api/netbox/ips` | Search/list IPs |
| `POST` | `/api/netbox/ips` | Create IP |
| `PATCH` | `/api/netbox/ips/{ip_id}` | Update IP |
| `DELETE` | `/api/netbox/ips/{ip_id}` | Delete IP |
| `GET` | `/api/netbox/device-types` | List device types |
| `GET` | `/api/netbox/sites` | List sites |
| `GET` | `/api/netbox/roles` | List roles |

### Observium

| Method | Path | Purpose |
|---|---|---|
| `GET` | `/api/observium/devices` | List devices |
| `GET` | `/api/observium/devices/{device_id}` | Device detail |
| `POST` | `/api/observium/devices` | Create device |
| `PATCH` | `/api/observium/devices/{device_id}` | Update device |
| `DELETE` | `/api/observium/devices/{device_id}` | Delete device |
| `POST` | `/api/observium/devices/{device_id}/refresh` | Run discovery/poller refresh |

### Export and Mapping

| Method | Path | Purpose |
|---|---|---|
| `POST` | `/api/exports/zabbix-to-observium` | Export one or many Zabbix hosts to Observium |
| `GET` | `/api/mapping` | Read field mapping |
| `PUT` | `/api/mapping` | Update field mapping |
| `GET` | `/api/health` | Healthcheck |

---

## Environment Variables

Defined in [.env.example](/home/randel/zabbix-netbox-unified/.env.example):

| Variable | Purpose |
|---|---|
| `ZABBIX_URL` | Base URL for Zabbix |
| `ZABBIX_TOKEN` | API token for Zabbix |
| `ZABBIX_USER` | Fallback username |
| `ZABBIX_PASS` | Fallback password |
| `NETBOX_URL` | Base URL for NetBox |
| `NETBOX_TOKEN` | API token for NetBox |
| `OBSERVIUM_BASE_URL` | Base URL for Observium |
| `OBSERVIUM_DB_HOST` | Observium DB host |
| `OBSERVIUM_DB_PORT` | Observium DB port |
| `OBSERVIUM_DB_NAME` | Observium DB name |
| `OBSERVIUM_DB_USER` | Observium DB user |
| `OBSERVIUM_DB_PASSWORD` | Observium DB password |
| `OBSERVIUM_CONTAINER` | Observium app container name |
| `APP_DB_PATH` | Local SQLite path used by the app |

---

## Private Company Configuration

Use the template in [company.example.json](/home/randel/zabbix-netbox-unified/backend/config/company.example.json) and keep your local override out of Git:

```bash
cp backend/config/company.example.json backend/config/company.local.json
```

Recommended use cases:

- branding and label overrides
- company-specific publishing rules
- internal metadata you do not want hardcoded into the repo

---

## Security and Publishing

Do not publish these files:

- `.env`
- `data/`
- `inventory.drawio`
- `*.log`
- `*.db`
- `*.sqlite`
- `*.sqlite3`
- `backend/config/company.local.json`

Tracked templates that are safe to keep:

- `.env.example`
- `backend/config/company.example.json`

The active ignore rules are defined in [.gitignore](/home/randel/zabbix-netbox-unified/.gitignore).

---

## Notes

- Credentials entered from the UI are stored in backend process memory.
- `.env` is the startup fallback.
- Generated inventory exports and local app data should stay outside the public repository.
- Production deployment should sit behind HTTPS or a reverse proxy.
