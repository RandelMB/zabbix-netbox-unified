import { useState, useEffect, useCallback, useRef } from "react";
import { api } from "../utils/api";
import { useLogs } from "../hooks/useLogs";
import { ConfirmModal } from "./ConfirmModal";

const TABS = ["General", "Interfaces", "IP Addresses", "Comments", "Custom Fields", "Sync", "Raw JSON"];

const EMPTY_DEVICE = {
  name: "",
  status: "active",
  site: "",
  role: "",
  device_type: "",
  asset_tag: "",
  serial: "",
  description: "",
  comments: "",
  custom_fields: {},
};

function errorMessage(error) {
  if (!error) return "Unknown error";
  if (typeof error === "string") return error;
  if (error instanceof Error) return error.message;
  if (typeof error === "object") return JSON.stringify(error);
  return String(error);
}

function normalizeIpAddress(address) {
  if (!address) return "";
  const trimmed = address.trim();
  if (trimmed.includes("/")) return trimmed;
  return trimmed.includes(":") ? `${trimmed}/128` : `${trimmed}/32`;
}

function ipHostPart(address) {
  return (address || "").split("/")[0].trim();
}

export function NetBoxEditor({ deviceId, onDataReady }) {
  const { addLog } = useLogs();
  const onDataReadyRef = useRef(onDataReady);
  const [activeDeviceId, setActiveDeviceId] = useState(deviceId);
  const [loading, setLoading] = useState(true);
  const [device, setDevice] = useState(EMPTY_DEVICE);
  const [interfaces, setInterfaces] = useState([]);
  const [ips, setIps] = useState([]);
  const [tab, setTab] = useState("General");
  const [confirm, setConfirm] = useState(null);
  const [saving, setSaving] = useState(false);
  const [newIface, setNewIface] = useState({ name: "", type: "1000base-t", enabled: true });
  const [addingIface, setAddingIface] = useState(false);
  const [newIp, setNewIp] = useState({ address: "", status: "active", interface_id: "" });
  const [addingIp, setAddingIp] = useState(false);
  const [existingIpMatches, setExistingIpMatches] = useState([]);
  const [selectedExistingIpId, setSelectedExistingIpId] = useState("");
  const [comments, setComments] = useState("");
  const [commentBuilder, setCommentBuilder] = useState({ ip: "", ssh: true, http: true, https: false, custom: "" });
  const [sites, setSites] = useState([]);
  const [roles, setRoles] = useState([]);
  const [deviceTypes, setDeviceTypes] = useState([]);
  const [syncPreview, setSyncPreview] = useState(null);
  const [syncLoading, setSyncLoading] = useState(false);
  const [syncConfig, setSyncConfig] = useState({ enabled: false, field_sources: {} });

  useEffect(() => {
    onDataReadyRef.current = onDataReady;
  }, [onDataReady]);

  useEffect(() => {
    setActiveDeviceId(deviceId);
    setTab("General");
    setAddingIface(false);
    setAddingIp(false);
    setExistingIpMatches([]);
    setSelectedExistingIpId("");
    setSyncPreview(null);
    setSyncConfig({ enabled: false, field_sources: {} });
  }, [deviceId]);

  const isCreateMode = activeDeviceId === "new";

  const loadOptions = useCallback(async () => {
    const [sitesR, rolesR, typesR] = await Promise.all([
      api.netboxSites(),
      api.netboxRoles(),
      api.netboxDeviceTypes(),
    ]);
    setSites(sitesR.result?.results || []);
    setRoles(rolesR.result?.results || []);
    setDeviceTypes(typesR.result?.results || []);
  }, []);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      await loadOptions();
      if (isCreateMode) {
        setDevice(EMPTY_DEVICE);
        setInterfaces([]);
        setIps([]);
        setComments("");
        setSyncPreview(null);
        if (onDataReadyRef.current) onDataReadyRef.current(EMPTY_DEVICE);
        setLoading(false);
        return;
      }

      const [devR, ifaceR, ipR] = await Promise.all([
        api.netboxDevice(activeDeviceId),
        api.netboxDeviceInterfaces(activeDeviceId),
        api.netboxIPs(activeDeviceId),
      ]);
      setDevice(devR.result);
      setInterfaces(ifaceR.result?.results || []);
      setIps(ipR.result?.results || []);
      setComments(devR.result?.comments || "");
      if (onDataReadyRef.current) onDataReadyRef.current(devR.result);
      addLog("ok", `Loaded NetBox device: ${devR.result?.name}`, devR.request);
    } catch (e) {
      addLog("err", "Failed to load NetBox device: " + errorMessage(e));
      if (isCreateMode) {
        setDevice(EMPTY_DEVICE);
      }
    }
    setLoading(false);
  }, [activeDeviceId, addLog, isCreateMode, loadOptions]);

  useEffect(() => {
    load();
  }, [load]);

  const loadSync = useCallback(async () => {
    if (isCreateMode) {
      setSyncPreview(null);
      setSyncConfig({ enabled: false, field_sources: {} });
      return;
    }
    setSyncLoading(true);
    try {
      const response = await api.netboxSync(activeDeviceId);
      const result = response.result || null;
      setSyncPreview(result);
      setSyncConfig({
        enabled: !!result?.profile?.enabled,
        field_sources: result?.profile?.field_sources || {},
      });
    } catch (e) {
      setSyncPreview(null);
      const message = errorMessage(e);
      if (message.includes("No saved correlation exists for this NetBox device")) {
        addLog("info", "NetBox sync unavailable: save a correlation first");
      } else {
        addLog("err", "NetBox sync load failed: " + message);
      }
    }
    setSyncLoading(false);
  }, [activeDeviceId, addLog, isCreateMode]);

  useEffect(() => {
    loadSync();
  }, [loadSync]);

  function updateField(path, value) {
    setDevice(prev => {
      const clone = JSON.parse(JSON.stringify(prev));
      const parts = path.split(".");
      let obj = clone;
      for (let i = 0; i < parts.length - 1; i += 1) {
        if (!obj[parts[i]]) obj[parts[i]] = {};
        obj = obj[parts[i]];
      }
      obj[parts[parts.length - 1]] = value;
      return clone;
    });
  }

  async function handleSave(payload) {
    setSaving(true);
    try {
      const r = await api.netboxUpdateDevice(activeDeviceId, payload);
      addLog("ok", `NetBox device updated: ${device.name}`, r);
      setConfirm(null);
      await load();
    } catch (e) {
      addLog("err", "NetBox update failed: " + errorMessage(e));
    }
    setSaving(false);
  }

  async function handleCreateDevice(payload) {
    setSaving(true);
    try {
      const r = await api.netboxCreateDevice(payload);
      const created = r.result || r.response;
      addLog("ok", `NetBox device created: ${created?.name || payload.name}`, r);
      setConfirm(null);
      setActiveDeviceId(created.id);
    } catch (e) {
      addLog("err", "NetBox create failed: " + errorMessage(e));
    }
    setSaving(false);
  }

  function buildGeneralPayload() {
    if (isCreateMode) {
      const payload = {
        name: device.name,
        status: device.status,
        site: Number(device.site),
        role: Number(device.role),
        device_type: Number(device.device_type),
        serial: device.serial || "",
        description: device.description || "",
        comments,
        custom_fields: device.custom_fields || {},
      };
      if ((device.asset_tag || "").trim()) payload.asset_tag = device.asset_tag.trim();
      return payload;
    }
    const payload = {
      name: device.name,
      status: device.status?.value || device.status,
      serial: device.serial || "",
      description: device.description || "",
      comments,
      custom_fields: device.custom_fields || {},
    };
    const assetTag = (device.asset_tag || "").trim();
    payload.asset_tag = assetTag || null;
    return payload;
  }

  function buildCommentFromIp() {
    const ip = commentBuilder.ip;
    const lines = [];
    if (ip) lines.push(`IP: ${ip}`);
    if (commentBuilder.ssh && ip) lines.push(`SSH: ssh://${ip}`);
    if (commentBuilder.http && ip) lines.push(`HTTP: http://${ip}`);
    if (commentBuilder.https && ip) lines.push(`HTTPS: https://${ip}`);
    if (commentBuilder.custom) lines.push(commentBuilder.custom);
    setComments(lines.join("\n"));
  }

  async function toggleArchiveSelf() {
    try {
      if (device.archived) {
        await api.restoreDevice("netbox", device.id);
        addLog("ok", `NetBox device restored: ${device.name}`);
      } else {
        await api.archiveDevice("netbox", device.id, { label: device.name });
        addLog("ok", `NetBox device archived: ${device.name}`);
      }
      await load();
    } catch (e) {
      addLog("err", "Archive toggle failed: " + errorMessage(e));
    }
  }

  async function lookupExistingIps(address) {
    const hostOnly = ipHostPart(address);
    if (!hostOnly) {
      setExistingIpMatches([]);
      setSelectedExistingIpId("");
      return [];
    }
    const search = await api.netboxIPs(null, hostOnly);
    const matches = search.result?.results || [];
    setExistingIpMatches(matches);
    setSelectedExistingIpId(matches[0]?.id ? String(matches[0].id) : "");
    return matches;
  }

  async function attachExistingIpToInterface(existingIpId, interfaceId) {
    let existing = existingIpMatches.find(item => String(item.id) === String(existingIpId));
    if (!existing) {
      const search = await api.netboxIPs(null);
      existing = (search.result?.results || []).find(item => String(item.id) === String(existingIpId));
    }
    if (!existing) {
      throw new Error(`Existing NetBox IP could not be found: ${existingIpId}`);
    }
    return existing;
  }

  async function handleCreateIp(payload) {
    setSaving(true);
    try {
      if (!payload.assigned_object_id) throw new Error("Select a NetBox interface first");
      const normalized = normalizeIpAddress(payload.address);
      const preMatches = await lookupExistingIps(normalized);
      let existing = null;
      let resolvedIpId = null;
      if (payload.existing_ip_id) {
        existing = preMatches.find(item => String(item.id) === String(payload.existing_ip_id)) || await attachExistingIpToInterface(payload.existing_ip_id, payload.assigned_object_id);
      } else if (preMatches.length > 0) {
        existing = preMatches[0];
      }

      if (existing) {
        if (existing.address !== normalized) {
          await api.netboxUpdateIP(existing.id, { address: normalized, status: payload.status });
          addLog("ok", `Existing IP mask updated: ${existing.address} -> ${normalized}`);
        } else {
          addLog("ok", `Existing IP found in NetBox: ${existing.address}`);
        }

        if (existing.assigned_object?.name) {
          addLog("ok", `Existing IP was assigned to interface ${existing.assigned_object.name}. Reassigning to selected interface.`);
        } else {
          addLog("ok", `Existing IP was unassigned. Assigning to selected interface.`);
        }

        await api.netboxUpdateIP(existing.id, {
          assigned_object_type: "dcim.interface",
          assigned_object_id: Number(payload.assigned_object_id),
          status: payload.status,
        });
        resolvedIpId = existing.id;
        addLog("ok", `Existing IP assigned to interface: ${normalized}`);
      } else {
        const created = await api.netboxCreateIP({ ...payload, address: normalized });
        resolvedIpId = created.result?.id || created.response?.id || null;
        addLog("ok", `IP created in NetBox: ${normalized}`);
      }

      if (resolvedIpId) {
        await api.netboxSetPrimaryIP(activeDeviceId, { ip_id: Number(resolvedIpId) });
        addLog("ok", `Primary IP updated in NetBox: ${normalized}`);
      }
      setAddingIp(false);
      setNewIp({ address: "", status: "active", interface_id: "" });
      setExistingIpMatches([]);
      setSelectedExistingIpId("");
      await load();
    } catch (e) {
      addLog("err", "NetBox IP apply failed: " + errorMessage(e));
    }
    setSaving(false);
    setConfirm(null);
  }

  function setSyncEnabled(value) {
    setSyncConfig(prev => ({ ...prev, enabled: value }));
  }

  function setSyncFieldSource(field, source) {
    setSyncConfig(prev => ({ ...prev, field_sources: { ...prev.field_sources, [field]: source } }));
  }

  async function saveSyncProfile() {
    setSaving(true);
    try {
      const response = await api.netboxUpdateSync(activeDeviceId, syncConfig);
      setSyncPreview(response.result?.preview || null);
      addLog("ok", `NetBox sync profile saved: ${device.name}`, response);
    } catch (e) {
      addLog("err", "NetBox sync profile save failed: " + errorMessage(e));
    }
    setSaving(false);
  }

  async function runSyncNow() {
    setSaving(true);
    try {
      const response = await api.netboxRunSync(activeDeviceId, syncConfig);
      setSyncPreview(response.result?.preview || null);
      addLog("ok", `NetBox sync executed: ${device.name}`, response);
      setConfirm(null);
      await load();
      await loadSync();
    } catch (e) {
      addLog("err", "NetBox sync failed: " + errorMessage(e));
    }
    setSaving(false);
  }

  if (loading) return <div style={{ padding: 20, color: "var(--text3)" }}>Loading...</div>;
  if (!device && !isCreateMode) return <div style={{ padding: 20, color: "var(--error)" }}>Device not found</div>;

  const statusVal = device.status?.value || device.status || "active";
  const actionableFields = new Set(
    Object.entries(syncPreview?.fields || {})
      .filter(([, meta]) => Object.values(meta?.candidates || {}).some(Boolean))
      .map(([field]) => field)
  );
  const fieldHighlightStyle = (field, currentValue) => {
    const text = typeof currentValue === "string" ? currentValue.trim() : currentValue;
    const isEmpty = currentValue === null || currentValue === undefined || text === "";
    if (!actionableFields.has(field)) return undefined;
    return {
      borderColor: isEmpty ? "var(--warn)" : "var(--accent2)",
      boxShadow: isEmpty ? "0 0 0 1px rgba(255,169,77,0.28)" : "0 0 0 1px rgba(0,153,255,0.22)",
      background: isEmpty ? "rgba(255,169,77,0.08)" : "rgba(0,153,255,0.06)",
    };
  };

  return (
    <div style={{ height: "100%", display: "flex", flexDirection: "column" }}>
      {confirm && (
        <ConfirmModal
          title={confirm.title}
          payload={confirm.payload}
          loading={saving}
          onCancel={() => setConfirm(null)}
          onConfirm={confirm.onConfirm}
        />
      )}

      <div style={{ padding: "12px 16px", borderBottom: "1px solid var(--border)", display: "flex", alignItems: "center", gap: 10 }}>
        <span className="tag tag-netbox">NB</span>
        <span style={{ fontFamily: "var(--font-mono)", fontWeight: 600, fontSize: 14 }}>{isCreateMode ? "New NetBox Device" : device.name}</span>
        {!isCreateMode && <span style={{ color: "var(--text3)", fontSize: 12 }}>{device.device_type?.display}</span>}
        {!isCreateMode && (
          <span style={{ marginLeft: "auto" }}>
            <span className={`tag ${statusVal === "active" ? "tag-ok" : "tag-warn"}`}>{statusVal}</span>
          </span>
        )}
      </div>

      <div className="tabs" style={{ padding: "0 16px" }}>
        {TABS.map(t => (
          <button key={t} className={`tab-btn ${tab === t ? "active" : ""}`} onClick={() => setTab(t)}>{t}</button>
        ))}
      </div>

      <div style={{ flex: 1, overflow: "auto", padding: 16 }}>
        {tab === "General" && (
          <div>
            <div className="grid-2">
              <div className="field-row">
                <label>Name</label>
                <input value={device.name || ""} onChange={e => updateField("name", e.target.value)} />
              </div>
              <div className="field-row">
                <label>Status</label>
                <select value={statusVal} onChange={e => updateField("status", e.target.value)}>
                  <option value="active">Active</option>
                  <option value="planned">Planned</option>
                  <option value="staged">Staged</option>
                  <option value="failed">Failed</option>
                  <option value="inventory">Inventory</option>
                  <option value="decommissioning">Decommissioning</option>
                  <option value="offline">Offline</option>
                </select>
              </div>

              {isCreateMode ? (
                <>
                  <div className="field-row">
                    <label>Site</label>
                    <select value={device.site || ""} onChange={e => updateField("site", e.target.value)}>
                      <option value="">Select site</option>
                      {sites.map(item => <option key={item.id} value={item.id}>{item.name}</option>)}
                    </select>
                  </div>
                  <div className="field-row">
                    <label>Role</label>
                    <select value={device.role || ""} onChange={e => updateField("role", e.target.value)}>
                      <option value="">Select role</option>
                      {roles.map(item => <option key={item.id} value={item.id}>{item.name}</option>)}
                    </select>
                  </div>
                  <div className="field-row">
                    <label>Device Type</label>
                    <select value={device.device_type || ""} onChange={e => updateField("device_type", e.target.value)}>
                      <option value="">Select device type</option>
                      {deviceTypes.map(item => <option key={item.id} value={item.id}>{item.display || item.model}</option>)}
                    </select>
                  </div>
                  <div className="field-row">
                    <label>Asset Tag</label>
                    <input value={device.asset_tag || ""} onChange={e => updateField("asset_tag", e.target.value)} style={fieldHighlightStyle("asset_tag", device.asset_tag)} />
                  </div>
                  <div className="field-row">
                    <label>Serial</label>
                    <input value={device.serial || ""} onChange={e => updateField("serial", e.target.value)} style={fieldHighlightStyle("serial", device.serial)} />
                  </div>
                </>
              ) : (
                <>
                  <div className="field-row">
                    <label>Device Type</label>
                    <input value={device.device_type?.display || ""} disabled style={{ opacity: 0.6 }} />
                  </div>
                  <div className="field-row">
                    <label>Site</label>
                    <input value={device.site?.name || ""} disabled style={{ opacity: 0.6 }} />
                  </div>
                  <div className="field-row">
                    <label>Role</label>
                    <input value={device.role?.display || device.device_role?.display || ""} disabled style={{ opacity: 0.6 }} />
                  </div>
                  <div className="field-row">
                    <label>Primary IP</label>
                    <input value={device.primary_ip4?.address || ""} disabled style={{ opacity: 0.6, ...(fieldHighlightStyle("primary_ip4", device.primary_ip4?.address) || {}) }} />
                  </div>
                  <div className="field-row">
                    <label>Asset Tag</label>
                    <input value={device.asset_tag || ""} onChange={e => updateField("asset_tag", e.target.value)} style={fieldHighlightStyle("asset_tag", device.asset_tag)} />
                  </div>
                  <div className="field-row">
                    <label>Serial</label>
                    <input value={device.serial || ""} onChange={e => updateField("serial", e.target.value)} style={fieldHighlightStyle("serial", device.serial)} />
                  </div>
                </>
              )}
            </div>

            <div className="field-row" style={{ marginTop: 12 }}>
              <label>Description</label>
              <textarea value={device.description || ""} onChange={e => updateField("description", e.target.value)} style={{ height: 84, resize: "vertical", ...(fieldHighlightStyle("description", device.description) || {}) }} />
            </div>

            {isCreateMode && (
              <div className="notice notice-info" style={{ marginTop: 12 }}>
                Create the device first. Then add interfaces and assign IPs to those interfaces.
              </div>
            )}
          </div>
        )}

        {tab === "Interfaces" && !isCreateMode && (
          <div>
            <table style={{ marginBottom: 16 }}>
              <thead>
                <tr><th>Name</th><th>Type</th><th>Enabled</th><th>MAC</th><th>Actions</th></tr>
              </thead>
              <tbody>
                {interfaces.map(iface => (
                  <tr key={iface.id}>
                    <td><input defaultValue={iface.name} id={`iface-name-${iface.id}`} style={{ width: 160 }} /></td>
                    <td><input defaultValue={iface.type?.value || iface.type} id={`iface-type-${iface.id}`} style={{ width: 140 }} /></td>
                    <td style={{ textAlign: "center" }}><input type="checkbox" defaultChecked={iface.enabled} id={`iface-en-${iface.id}`} /></td>
                    <td style={{ color: "var(--text3)" }}>{iface.mac_address || "-"}</td>
                    <td>
                      <div className="flex-gap">
                        <button className="btn-secondary" style={{ padding: "2px 8px", fontSize: 10 }} onClick={() => {
                          const payload = {
                            name: document.getElementById(`iface-name-${iface.id}`)?.value || iface.name,
                            type: document.getElementById(`iface-type-${iface.id}`)?.value || iface.type?.value,
                            enabled: document.getElementById(`iface-en-${iface.id}`)?.checked ?? iface.enabled,
                          };
                          setConfirm({
                            title: `Update Interface: ${iface.name}`,
                            payload,
                            onConfirm: async p => {
                              setSaving(true);
                              try {
                                await api.netboxUpdateInterface(iface.id, p);
                                addLog("ok", `Interface updated: ${iface.name}`);
                                await load();
                              } catch (e) {
                                addLog("err", errorMessage(e));
                              }
                              setSaving(false);
                              setConfirm(null);
                            },
                          });
                        }}>Save</button>
                        <button className="btn-danger" style={{ padding: "2px 8px", fontSize: 10 }} onClick={async () => {
                          if (!window.confirm(`Delete interface ${iface.name}?`)) return;
                          try {
                            await api.netboxDeleteInterface(iface.id);
                            addLog("ok", `Interface deleted: ${iface.name}`);
                            await load();
                          } catch (e) {
                            addLog("err", errorMessage(e));
                          }
                        }}>x</button>
                      </div>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
            {!addingIface ? (
              <button className="btn-secondary" onClick={() => setAddingIface(true)}>+ Add Interface</button>
            ) : (
              <div className="section" style={{ padding: 12 }}>
                <div className="grid-3" style={{ marginBottom: 8 }}>
                  <div className="field-row"><label>Name</label><input value={newIface.name} onChange={e => setNewIface(p => ({ ...p, name: e.target.value }))} /></div>
                  <div className="field-row"><label>Type</label><input value={newIface.type} onChange={e => setNewIface(p => ({ ...p, type: e.target.value }))} /></div>
                </div>
                <div className="flex-gap">
                  <button className="btn-primary" onClick={() => setConfirm({
                    title: "Create Interface in NetBox",
                    payload: { ...newIface, device: activeDeviceId },
                    onConfirm: async p => {
                      setSaving(true);
                      try {
                        await api.netboxCreateInterface(p);
                        addLog("ok", "Interface created");
                        setAddingIface(false);
                        await load();
                      } catch (e) {
                        addLog("err", errorMessage(e));
                      }
                      setSaving(false);
                      setConfirm(null);
                    },
                  })}>Preview & Create</button>
                  <button className="btn-secondary" onClick={() => setAddingIface(false)}>Cancel</button>
                </div>
              </div>
            )}
          </div>
        )}

        {tab === "IP Addresses" && !isCreateMode && (
          <div>
            <table style={{ marginBottom: 16 }}>
              <thead>
                <tr><th>Address</th><th>Status</th><th>Interface</th><th>Description</th><th>Actions</th></tr>
              </thead>
              <tbody>
                {ips.map(ip => (
                  <tr key={ip.id}>
                    <td style={{ fontFamily: "var(--font-mono)", color: "var(--accent2)" }}>{ip.address}</td>
                    <td><span className={`tag ${ip.status?.value === "active" ? "tag-ok" : "tag-warn"}`}>{ip.status?.value || ip.status}</span></td>
                    <td style={{ color: "var(--text2)" }}>{ip.assigned_object?.name || "-"}</td>
                    <td><input defaultValue={ip.description} id={`ip-desc-${ip.id}`} /></td>
                    <td>
                      <button className="btn-secondary" style={{ padding: "2px 8px", fontSize: 10 }} onClick={() => setConfirm({
                        title: `Update IP: ${ip.address}`,
                        payload: { description: document.getElementById(`ip-desc-${ip.id}`)?.value },
                        onConfirm: async p => {
                          setSaving(true);
                          try {
                            await api.netboxUpdateIP(ip.id, p);
                            addLog("ok", `IP updated: ${ip.address}`);
                            await load();
                          } catch (e) {
                            addLog("err", errorMessage(e));
                          }
                          setSaving(false);
                          setConfirm(null);
                        },
                      })}>Save</button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
            {!addingIp ? (
              <button className="btn-secondary" onClick={() => setAddingIp(true)}>+ Add or Assign IP</button>
            ) : (
              <div className="section" style={{ padding: 12 }}>
                <div className="grid-2" style={{ marginBottom: 8 }}>
                  <div className="field-row"><label>Address (CIDR)</label><input placeholder="10.0.0.1/24" value={newIp.address} onChange={e => setNewIp(p => ({ ...p, address: e.target.value }))} onBlur={() => lookupExistingIps(newIp.address).catch(err => addLog("err", errorMessage(err)))} /></div>
                  <div className="field-row">
                    <label>Status</label>
                    <select value={newIp.status} onChange={e => setNewIp(p => ({ ...p, status: e.target.value }))}>
                      <option value="active">Active</option>
                      <option value="reserved">Reserved</option>
                      <option value="deprecated">Deprecated</option>
                      <option value="dhcp">DHCP</option>
                    </select>
                  </div>
                </div>
                <div className="field-row" style={{ marginBottom: 8 }}>
                  <label>Interface</label>
                  <select value={newIp.interface_id} onChange={e => setNewIp(p => ({ ...p, interface_id: e.target.value }))}>
                    <option value="">Select interface</option>
                    {interfaces.map(iface => <option key={iface.id} value={iface.id}>{iface.name}</option>)}
                  </select>
                </div>
                <div className="field-row" style={{ marginBottom: 8 }}>
                  <label>Existing NetBox IP</label>
                  <div className="flex-gap" style={{ alignItems: "center", flexWrap: "wrap" }}>
                    <select value={selectedExistingIpId} onChange={e => setSelectedExistingIpId(e.target.value)} style={{ minWidth: 280 }}>
                      <option value="">Create if not found</option>
                      {existingIpMatches.map(ip => (
                        <option key={ip.id} value={ip.id}>
                          {ip.address} {ip.assigned_object?.name ? `-> ${ip.assigned_object.name}` : "-> unassigned"}
                        </option>
                      ))}
                    </select>
                    <button className="btn-secondary" type="button" onClick={() => lookupExistingIps(newIp.address).catch(err => addLog("err", errorMessage(err)))}>
                      Search Existing
                    </button>
                  </div>
                </div>
                <div className="notice notice-info" style={{ marginBottom: 8 }}>
                  If you select an existing NetBox IP, it will be assigned to the selected interface. If no existing IP is selected, the editor will create it.
                </div>
                <div className="flex-gap">
                  <button className="btn-primary" onClick={() => setConfirm({
                    title: selectedExistingIpId ? "Assign Existing IP in NetBox" : "Create or Assign IP in NetBox",
                    payload: { ...newIp, address: normalizeIpAddress(newIp.address), existing_ip_id: selectedExistingIpId || undefined, assigned_object_type: "dcim.interface", assigned_object_id: Number(newIp.interface_id) },
                    onConfirm: handleCreateIp,
                  })}>Preview & Apply</button>
                  <button className="btn-secondary" onClick={() => setAddingIp(false)}>Cancel</button>
                </div>
              </div>
            )}
          </div>
        )}

        {tab === "Comments" && (
          <div>
            <div className="field-row">
              <label>Comments (editable)</label>
              <textarea value={comments} onChange={e => setComments(e.target.value)} style={{ height: 200, resize: "vertical", ...(fieldHighlightStyle("comments", comments) || {}) }} />
            </div>
          </div>
        )}

        {tab === "Custom Fields" && (
          <div>
            {device.custom_fields && Object.keys(device.custom_fields).length > 0 ? (
              <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 8 }}>
                {Object.entries(device.custom_fields).map(([k, v]) => (
                  <div className="field-row" key={k}>
                    <label>{k}</label>
                    <input value={v === null ? "" : String(v)} onChange={e => updateField(`custom_fields.${k}`, e.target.value)} />
                  </div>
                ))}
              </div>
            ) : (
              <div style={{ color: "var(--text3)", fontFamily: "var(--font-mono)" }}>No custom fields</div>
            )}
          </div>
        )}

        {tab === "Sync" && !isCreateMode && (
          <div>
            <div className="section" style={{ marginBottom: 16 }}>
              <div className="section-header" style={{ display: "flex", alignItems: "center", justifyContent: "space-between" }}>
                <span>Synchronization</span>
                <button className="btn-secondary" onClick={loadSync} disabled={syncLoading}>
                  {syncLoading ? "Refreshing..." : "Refresh Sources"}
                </button>
              </div>
              <div className="section-body">
                {!syncLoading && !syncPreview?.correlation && (
                  <div className="notice notice-info" style={{ marginBottom: 0 }}>
                    This NetBox device does not have a saved correlation with Zabbix and Observium yet. Save a correlation first to unlock synchronization.
                  </div>
                )}
                {syncLoading && <div style={{ color: "var(--text3)" }}>Loading correlated sources...</div>}
                {!syncLoading && syncPreview?.correlation && (
                  <>
                    <div style={{ marginBottom: 12, color: "var(--text2)" }}>
                      Correlation #{syncPreview.correlation.id} {syncPreview.correlation.label ? `· ${syncPreview.correlation.label}` : ""}
                    </div>
                    <div className="grid-2">
                      <div className="section" style={{ padding: 12 }}>
                        <div style={{ fontFamily: "var(--font-mono)", fontSize: 12, marginBottom: 8 }}>Zabbix</div>
                        <div style={{ color: "var(--text2)", fontSize: 12 }}>Host: {syncPreview.sources?.zabbix?.host || "-"}</div>
                        <div style={{ color: "var(--text2)", fontSize: 12 }}>Visible: {syncPreview.sources?.zabbix?.name || "-"}</div>
                      </div>
                      <div className="section" style={{ padding: 12 }}>
                        <div style={{ fontFamily: "var(--font-mono)", fontSize: 12, marginBottom: 8 }}>Observium</div>
                        <div style={{ color: "var(--text2)", fontSize: 12 }}>Host: {syncPreview.sources?.observium?.hostname || "-"}</div>
                        <div style={{ color: "var(--text2)", fontSize: 12 }}>sysName: {syncPreview.sources?.observium?.sysName || "-"}</div>
                      </div>
                    </div>
                  </>
                )}
              </div>
            </div>

            {syncPreview?.correlation && (
              <>
                <div className="section" style={{ marginBottom: 16 }}>
                  <div className="section-header"><span>Profile</span></div>
                  <div className="section-body">
                    <div className="field-row" style={{ marginBottom: 12 }}>
                      <label>Enable Synchronization Profile</label>
                      <select value={syncConfig.enabled ? "1" : "0"} onChange={e => setSyncEnabled(e.target.value === "1")}>
                        <option value="0">Disabled</option>
                        <option value="1">Enabled</option>
                      </select>
                    </div>
                    <div className="notice notice-info" style={{ marginBottom: 8 }}>
                      Choose the preferred source for each field. `platform` uses `OS + version` and creates the missing NetBox platform automatically. `primary_ip4` creates or reuses the IP in NetBox and assigns it as the device primary IP.
                    </div>
                    {(Object.entries(syncPreview.fields || [])).map(([field, meta]) => (
                      <div key={field} className="section" style={{ padding: 12, marginBottom: 12 }}>
                        <div style={{ display: "grid", gridTemplateColumns: "160px 160px 1fr 1fr 1fr", gap: 10, alignItems: "end" }}>
                          <div className="field-row">
                            <label>Field</label>
                            <input value={field} readOnly />
                          </div>
                          <div className="field-row">
                            <label>Source</label>
                            <select value={syncConfig.field_sources[field] || meta.selected_source || ""} onChange={e => setSyncFieldSource(field, e.target.value)}>
                              <option value="zabbix">Zabbix</option>
                              <option value="observium">Observium</option>
                            </select>
                          </div>
                          <div className="field-row">
                            <label>Current NetBox</label>
                            <input value={meta.current_value || ""} readOnly />
                          </div>
                          <div className="field-row">
                            <label>Zabbix</label>
                            <input value={meta.candidates?.zabbix || ""} readOnly />
                          </div>
                          <div className="field-row">
                            <label>Observium</label>
                            <input value={meta.candidates?.observium || ""} readOnly />
                          </div>
                        </div>
                      </div>
                    ))}
                    <div className="flex-gap" style={{ marginTop: 12 }}>
                      <button className="btn-secondary" onClick={saveSyncProfile} disabled={saving}>
                        Save Sync Profile
                      </button>
                      <button
                        className="btn-primary"
                        onClick={() => setConfirm({
                          title: `Run NetBox Sync: ${device.name}`,
                          payload: syncConfig,
                          onConfirm: runSyncNow,
                        })}
                        disabled={saving}
                      >
                        Preview & Run Sync
                      </button>
                    </div>
                  </div>
                </div>
              </>
            )}
          </div>
        )}

        {tab === "Raw JSON" && (
          <div>
            <div style={{ marginBottom: 8, color: "var(--text3)", fontSize: 11 }}>Device</div>
            <div className="json-preview" style={{ marginBottom: 12, maxHeight: 250 }}>{JSON.stringify(device, null, 2)}</div>
            <div style={{ marginBottom: 8, color: "var(--text3)", fontSize: 11 }}>Interfaces</div>
            <div className="json-preview" style={{ marginBottom: 12, maxHeight: 200 }}>{JSON.stringify(interfaces, null, 2)}</div>
            <div style={{ marginBottom: 8, color: "var(--text3)", fontSize: 11 }}>IPs</div>
            <div className="json-preview" style={{ maxHeight: 200 }}>{JSON.stringify(ips, null, 2)}</div>
          </div>
        )}
      </div>

      {tab !== "Raw JSON" && tab !== "Interfaces" && tab !== "IP Addresses" && tab !== "Sync" && (
        <div style={{ padding: "12px 16px", borderTop: "1px solid var(--border)", display: "flex", gap: 8, flexWrap: "wrap" }}>
          <button className="btn-primary" onClick={() => {
            const payload = buildGeneralPayload();
            if (tab === "Comments") payload.comments = comments;
            if (tab === "Custom Fields") payload.custom_fields = device.custom_fields;
            if (isCreateMode) {
              setConfirm({ title: `Create NetBox Device: ${device.name || "new-device"}`, payload, onConfirm: handleCreateDevice });
              return;
            }
            setConfirm({ title: `Update NetBox Device: ${device.name}`, payload, onConfirm: handleSave });
          }}>
            {isCreateMode ? "Preview & Create in NetBox" : "Preview & Save to NetBox"}
          </button>
          <button className="btn-secondary" onClick={load}>Reload</button>
          {!isCreateMode && <button className="btn-secondary" onClick={toggleArchiveSelf}>{device.archived ? "Restore" : "Archive"}</button>}
        </div>
      )}
    </div>
  );
}
