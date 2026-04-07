const BASE = process.env.REACT_APP_API_URL || "";

function formatErrorDetail(detail) {
  if (!detail) return "";
  if (typeof detail === "string") return detail;
  if (Array.isArray(detail)) {
    return detail.map(item => formatErrorDetail(item)).filter(Boolean).join(" | ");
  }
  if (typeof detail === "object") {
    if (typeof detail.msg === "string") return detail.msg;
    if (typeof detail.detail === "string") return detail.detail;
    return JSON.stringify(detail);
  }
  return String(detail);
}

async function req(method, path, body) {
  const opts = {
    method,
    headers: { "Content-Type": "application/json" },
  };
  if (body) opts.body = JSON.stringify(body);
  const r = await fetch(`${BASE}${path}`, opts);
  const data = await r.json().catch(() => ({}));
  if (!r.ok) {
    const message = formatErrorDetail(data.detail) || formatErrorDetail(data) || `${method} ${path} failed with status ${r.status}`;
    throw new Error(message);
  }
  return data;
}

export const api = {
  get: (p) => req("GET", p),
  post: (p, b) => req("POST", p, b),
  patch: (p, b) => req("PATCH", p, b),
  put: (p, b) => req("PUT", p, b),
  delete: (p) => req("DELETE", p),

  // Credentials
  getCredentials: () => req("GET", "/api/credentials"),
  setCredentials: (b) => req("POST", "/api/credentials", b),

  // Health
  checkZabbix: () => req("GET", "/api/check/zabbix"),
  checkNetbox: () => req("GET", "/api/check/netbox"),
  checkObservium: () => req("GET", "/api/check/observium"),

  // Zabbix
  zabbixHosts: (limit = 500, archived = "exclude") => req("GET", `/api/zabbix/hosts?limit=${limit}&archived=${encodeURIComponent(archived)}`),
  zabbixHost: (id) => req("GET", `/api/zabbix/hosts/${id}`),
  zabbixUpdateHost: (id, b) => req("PATCH", `/api/zabbix/hosts/${id}`, b),
  zabbixCreateHost: (b) => req("POST", "/api/zabbix/hosts", b),
  zabbixInterfaces: (hostId) => req("GET", `/api/zabbix/interfaces/${hostId}`),
  zabbixCreateInterface: (b) => req("POST", "/api/zabbix/interfaces", b),
  zabbixUpdateInterface: (id, b) => req("PATCH", `/api/zabbix/interfaces/${id}`, b),
  zabbixDeleteInterface: (id) => req("DELETE", `/api/zabbix/interfaces/${id}`),
  zabbixGroups: () => req("GET", "/api/zabbix/groups"),

  // NetBox
  netboxDevices: (limit = 500, archived = "exclude") => req("GET", `/api/netbox/devices?limit=${limit}&archived=${encodeURIComponent(archived)}`),
  netboxDevice: (id) => req("GET", `/api/netbox/devices/${id}`),
  netboxUpdateDevice: (id, b) => req("PATCH", `/api/netbox/devices/${id}`, b),
  netboxCreateDevice: (b) => req("POST", "/api/netbox/devices", b),
  netboxSetPrimaryIP: (deviceId, b) => req("POST", `/api/netbox/devices/${deviceId}/primary-ip`, b),
  netboxDeviceInterfaces: (id) => req("GET", `/api/netbox/devices/${id}/interfaces`),
  netboxUpdateInterface: (id, b) => req("PATCH", `/api/netbox/interfaces/${id}`, b),
  netboxCreateInterface: (b) => req("POST", "/api/netbox/interfaces", b),
  netboxDeleteInterface: (id) => req("DELETE", `/api/netbox/interfaces/${id}`),
  netboxIPs: (deviceId, address = "") => {
    const params = new URLSearchParams({ limit: "200" });
    if (deviceId !== undefined && deviceId !== null && String(deviceId) !== "") params.set("device_id", String(deviceId));
    if (address) params.set("address", address);
    return req("GET", `/api/netbox/ips?${params.toString()}`);
  },
  netboxCreateIP: (b) => req("POST", "/api/netbox/ips", b),
  netboxUpdateIP: (id, b) => req("PATCH", `/api/netbox/ips/${id}`, b),
  netboxDeleteIP: (id) => req("DELETE", `/api/netbox/ips/${id}`),
  netboxSites: () => req("GET", "/api/netbox/sites"),
  netboxDeviceTypes: () => req("GET", "/api/netbox/device-types"),
  netboxPlatforms: () => req("GET", "/api/netbox/platforms"),
  netboxLocations: (siteId = "") => req("GET", `/api/netbox/locations${siteId ? `?site_id=${encodeURIComponent(siteId)}` : ""}`),
  netboxRoles: () => req("GET", "/api/netbox/roles"),
  netboxEnrichment: (deviceId) => req("GET", `/api/netbox/devices/${deviceId}/enrichment`),
  netboxApplyEnrichment: (deviceId, b) => req("POST", `/api/netbox/devices/${deviceId}/enrichment/apply`, b),
  netboxFixPrimaryIpsCorrelated: (b = {}) => req("POST", "/api/netbox/enrichment/fix-primary-ip4-correlated", b),

  // Observium
  observiumDevices: (limit = 500, search = "", archived = "exclude") => req("GET", `/api/observium/devices?limit=${limit}&search=${encodeURIComponent(search)}&archived=${encodeURIComponent(archived)}`),
  observiumDevice: (id) => req("GET", `/api/observium/devices/${id}`),
  observiumCreateDevice: (b) => req("POST", "/api/observium/devices", b),
  observiumUpdateDevice: (id, b) => req("PATCH", `/api/observium/devices/${id}`, b),
  observiumDeleteDevice: (id) => req("DELETE", `/api/observium/devices/${id}`),
  observiumRefreshDevice: (id) => req("POST", `/api/observium/devices/${id}/refresh`),

  // Archive
  archives: (source) => req("GET", `/api/archive/${source}`),
  archiveDevice: (source, id, b = {}) => req("POST", `/api/archive/${source}/${id}`, b),
  restoreDevice: (source, id) => req("DELETE", `/api/archive/${source}/${id}`),
  bulkArchive: (b) => req("POST", "/api/archive/bulk", b),

  // Export / sync
  exportZabbixToObservium: (b) => req("POST", "/api/exports/zabbix-to-observium", b),
  exportLogs: (limit = 100) => req("GET", `/api/export-logs?limit=${limit}`),

  // Correlations
  correlations: () => req("GET", "/api/correlations"),
  correlationMatch: (source, externalId) => req("GET", `/api/correlations/match?source=${encodeURIComponent(source)}&external_id=${encodeURIComponent(externalId)}`),
  saveCorrelation: (b) => req("POST", "/api/correlations/link", b),
  unlinkCorrelation: (groupId, source) => req("DELETE", `/api/correlations/${groupId}/${source}`),

  // Mapping
  getMapping: () => req("GET", "/api/mapping"),
  setMapping: (b) => req("PUT", "/api/mapping", b),
};
