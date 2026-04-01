import { useState, useEffect, useCallback } from "react";
import { api } from "../utils/api";
import { useLogs } from "../hooks/useLogs";
import { ConfirmModal } from "./ConfirmModal";

const TABS = ["General", "Interfaces", "IP Addresses", "Comments", "Custom Fields", "Raw JSON"];

export function NetBoxEditor({ deviceId, onDataReady }) {
  const { addLog } = useLogs();
  const [loading, setLoading] = useState(true);
  const [device, setDevice] = useState(null);
  const [interfaces, setInterfaces] = useState([]);
  const [ips, setIps] = useState([]);
  const [tab, setTab] = useState("General");
  const [confirm, setConfirm] = useState(null);
  const [saving, setSaving] = useState(false);
  const [newIface, setNewIface] = useState({ name: "", type: "1000base-t", enabled: true });
  const [addingIface, setAddingIface] = useState(false);
  const [newIp, setNewIp] = useState({ address: "", status: "active" });
  const [addingIp, setAddingIp] = useState(false);
  const [comments, setComments] = useState("");
  const [commentBuilder, setCommentBuilder] = useState({ ip: "", ssh: true, http: true, https: false, custom: "" });

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const [devR, ifaceR, ipR] = await Promise.all([
        api.netboxDevice(deviceId),
        api.netboxDeviceInterfaces(deviceId),
        api.netboxIPs(deviceId),
      ]);
      setDevice(devR.result);
      setInterfaces(ifaceR.result?.results || []);
      setIps(ipR.result?.results || []);
      setComments(devR.result?.comments || "");
      onDataReady && onDataReady(devR.result);
      addLog("ok", `Loaded NetBox device: ${devR.result?.name}`, devR.request);
    } catch (e) {
      addLog("err", "Failed to load NetBox device: " + e.message);
    }
    setLoading(false);
  }, [deviceId]);

  useEffect(() => { load(); }, [load]);

  function updateField(path, value) {
    setDevice(prev => {
      const clone = JSON.parse(JSON.stringify(prev));
      const parts = path.split(".");
      let obj = clone;
      for (let i = 0; i < parts.length - 1; i++) {
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
      const r = await api.netboxUpdateDevice(deviceId, payload);
      addLog("ok", `NetBox device updated: ${device.name}`, r);
      setConfirm(null);
      await load();
    } catch (e) {
      addLog("err", "NetBox update failed: " + e.message);
    }
    setSaving(false);
  }

  function buildGeneralPayload() {
    return {
      name: device.name,
      status: device.status?.value || device.status,
      comments: comments,
      custom_fields: device.custom_fields || {},
    };
  }

  function buildCommentFromIp() {
    const ip = commentBuilder.ip;
    let lines = [];
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
      addLog("err", "Archive toggle failed: " + e.message);
    }
  }

  if (loading) return <div style={{ padding: 20, color: "var(--text3)" }}>Loading…</div>;
  if (!device) return <div style={{ padding: 20, color: "var(--error)" }}>Device not found</div>;

  const statusVal = device.status?.value || device.status;

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

      {/* Header */}
      <div style={{ padding: "12px 16px", borderBottom: "1px solid var(--border)", display: "flex", alignItems: "center", gap: 10 }}>
        <span className="tag tag-netbox">NB</span>
        <span style={{ fontFamily: "var(--font-mono)", fontWeight: 600, fontSize: 14 }}>{device.name}</span>
        <span style={{ color: "var(--text3)", fontSize: 12 }}>{device.device_type?.display}</span>
        <span style={{ marginLeft: "auto" }}>
          <span className={`tag ${statusVal === "active" ? "tag-ok" : "tag-warn"}`}>{statusVal}</span>
        </span>
      </div>

      {/* Tabs */}
      <div className="tabs" style={{ padding: "0 16px" }}>
        {TABS.map(t => (
          <button key={t} className={`tab-btn ${tab === t ? "active" : ""}`} onClick={() => setTab(t)}>{t}</button>
        ))}
      </div>

      <div style={{ flex: 1, overflow: "auto", padding: 16 }}>
        {/* GENERAL */}
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
                <input value={device.primary_ip4?.address || ""} disabled style={{ opacity: 0.6 }} />
              </div>
            </div>
          </div>
        )}

        {/* INTERFACES */}
        {tab === "Interfaces" && (
          <div>
            <table style={{ marginBottom: 16 }}>
              <thead>
                <tr><th>Name</th><th>Type</th><th>Enabled</th><th>MAC</th><th>Actions</th></tr>
              </thead>
              <tbody>
                {interfaces.map(iface => (
                  <tr key={iface.id}>
                    <td>
                      <input defaultValue={iface.name} id={`iface-name-${iface.id}`} style={{ width: 160 }} />
                    </td>
                    <td>
                      <input defaultValue={iface.type?.value || iface.type} id={`iface-type-${iface.id}`} style={{ width: 140 }} />
                    </td>
                    <td style={{ textAlign: "center" }}>
                      <input type="checkbox" defaultChecked={iface.enabled} id={`iface-en-${iface.id}`} />
                    </td>
                    <td style={{ color: "var(--text3)" }}>{iface.mac_address || "—"}</td>
                    <td>
                      <div className="flex-gap">
                        <button className="btn-secondary" style={{ padding: "2px 8px", fontSize: 10 }}
                          onClick={() => {
                            const payload = {
                              name: document.getElementById(`iface-name-${iface.id}`)?.value || iface.name,
                              type: document.getElementById(`iface-type-${iface.id}`)?.value || iface.type?.value,
                              enabled: document.getElementById(`iface-en-${iface.id}`)?.checked ?? iface.enabled,
                            };
                            setConfirm({
                              title: `Update Interface: ${iface.name}`,
                              payload,
                              onConfirm: async (p) => {
                                setSaving(true);
                                try { await api.netboxUpdateInterface(iface.id, p); addLog("ok", `Interface updated: ${iface.name}`); await load(); } catch (e) { addLog("err", e.message); }
                                setSaving(false); setConfirm(null);
                              }
                            });
                          }}>Save</button>
                        <button className="btn-danger" style={{ padding: "2px 8px", fontSize: 10 }}
                          onClick={async () => {
                            if (!window.confirm(`Delete interface ${iface.name}?`)) return;
                            try { await api.netboxDeleteInterface(iface.id); addLog("ok", `Interface deleted: ${iface.name}`); await load(); }
                            catch (e) { addLog("err", e.message); }
                          }}>✕</button>
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
                  <div className="field-row"><label>Name</label>
                    <input value={newIface.name} onChange={e => setNewIface(p => ({ ...p, name: e.target.value }))} />
                  </div>
                  <div className="field-row"><label>Type</label>
                    <input value={newIface.type} onChange={e => setNewIface(p => ({ ...p, type: e.target.value }))} />
                  </div>
                </div>
                <div className="flex-gap">
                  <button className="btn-primary" onClick={() => setConfirm({
                    title: "Create Interface in NetBox",
                    payload: { ...newIface, device: deviceId },
                    onConfirm: async (p) => {
                      setSaving(true);
                      try { await api.netboxCreateInterface(p); addLog("ok", "Interface created"); setAddingIface(false); await load(); } catch (e) { addLog("err", e.message); }
                      setSaving(false); setConfirm(null);
                    }
                  })}>Preview & Create</button>
                  <button className="btn-secondary" onClick={() => setAddingIface(false)}>Cancel</button>
                </div>
              </div>
            )}
          </div>
        )}

        {/* IP ADDRESSES */}
        {tab === "IP Addresses" && (
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
                    <td style={{ color: "var(--text2)" }}>{ip.assigned_object?.name || "—"}</td>
                    <td><input defaultValue={ip.description} id={`ip-desc-${ip.id}`} /></td>
                    <td>
                      <button className="btn-secondary" style={{ padding: "2px 8px", fontSize: 10 }}
                        onClick={() => setConfirm({
                          title: `Update IP: ${ip.address}`,
                          payload: { description: document.getElementById(`ip-desc-${ip.id}`)?.value },
                          onConfirm: async (p) => {
                            setSaving(true);
                            try { await api.netboxUpdateIP(ip.id, p); addLog("ok", `IP updated: ${ip.address}`); await load(); } catch (e) { addLog("err", e.message); }
                            setSaving(false); setConfirm(null);
                          }
                        })}>Save</button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
            {!addingIp ? (
              <button className="btn-secondary" onClick={() => setAddingIp(true)}>+ Add IP</button>
            ) : (
              <div className="section" style={{ padding: 12 }}>
                <div className="grid-2" style={{ marginBottom: 8 }}>
                  <div className="field-row"><label>Address (CIDR)</label>
                    <input placeholder="10.0.0.1/24" value={newIp.address} onChange={e => setNewIp(p => ({ ...p, address: e.target.value }))} />
                  </div>
                  <div className="field-row"><label>Status</label>
                    <select value={newIp.status} onChange={e => setNewIp(p => ({ ...p, status: e.target.value }))}>
                      <option value="active">Active</option><option value="reserved">Reserved</option>
                      <option value="deprecated">Deprecated</option><option value="dhcp">DHCP</option>
                    </select>
                  </div>
                </div>
                <div className="flex-gap">
                  <button className="btn-primary" onClick={() => setConfirm({
                    title: "Create IP in NetBox",
                    payload: { ...newIp, assigned_object_type: "dcim.device", assigned_object_id: deviceId },
                    onConfirm: async (p) => {
                      setSaving(true);
                      try { await api.netboxCreateIP(p); addLog("ok", "IP created"); setAddingIp(false); await load(); } catch (e) { addLog("err", e.message); }
                      setSaving(false); setConfirm(null);
                    }
                  })}>Preview & Create</button>
                  <button className="btn-secondary" onClick={() => setAddingIp(false)}>Cancel</button>
                </div>
              </div>
            )}
          </div>
        )}

        {/* COMMENTS */}
        {tab === "Comments" && (
          <div>
            <div className="section" style={{ marginBottom: 16 }}>
              <div className="section-header"><span>Auto-build from IP</span></div>
              <div className="section-body">
                <div className="grid-2" style={{ marginBottom: 8 }}>
                  <div className="field-row"><label>IP Address</label>
                    <input value={commentBuilder.ip} onChange={e => setCommentBuilder(p => ({ ...p, ip: e.target.value }))} placeholder="10.0.0.1" />
                  </div>
                </div>
                <div className="flex-gap" style={{ marginBottom: 8, flexWrap: "wrap" }}>
                  {["ssh", "http", "https"].map(proto => (
                    <label key={proto} style={{ display: "flex", alignItems: "center", gap: 4, cursor: "pointer", fontSize: 12 }}>
                      <input type="checkbox" checked={commentBuilder[proto]} onChange={e => setCommentBuilder(p => ({ ...p, [proto]: e.target.checked }))} />
                      {proto.toUpperCase()}
                    </label>
                  ))}
                </div>
                <div className="field-row">
                  <label>Custom lines</label>
                  <input value={commentBuilder.custom} onChange={e => setCommentBuilder(p => ({ ...p, custom: e.target.value }))} placeholder="Extra line" />
                </div>
                <button className="btn-secondary" onClick={buildCommentFromIp}>Generate Comment →</button>
              </div>
            </div>
            <div className="field-row">
              <label>Comments (editable)</label>
              <textarea value={comments} onChange={e => setComments(e.target.value)}
                style={{ height: 200, resize: "vertical" }} />
            </div>
          </div>
        )}

        {/* CUSTOM FIELDS */}
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

        {/* RAW JSON */}
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

      {/* Footer */}
      {tab !== "Raw JSON" && tab !== "Interfaces" && tab !== "IP Addresses" && (
        <div style={{ padding: "12px 16px", borderTop: "1px solid var(--border)", display: "flex", gap: 8 }}>
          <button className="btn-primary" onClick={() => {
            const payload = buildGeneralPayload();
            if (tab === "Comments") payload.comments = comments;
            if (tab === "Custom Fields") payload.custom_fields = device.custom_fields;
            setConfirm({ title: `Update NetBox Device: ${device.name}`, payload, onConfirm: handleSave });
          }}>
            Preview & Save to NetBox
          </button>
          <button className="btn-secondary" onClick={load}>↺ Reload</button>
          <button className="btn-secondary" onClick={toggleArchiveSelf}>{device.archived ? "Restore" : "Archive"}</button>
        </div>
      )}
    </div>
  );
}
