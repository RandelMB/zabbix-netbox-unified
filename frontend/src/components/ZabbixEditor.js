import { useState, useEffect, useCallback, useRef } from "react";
import { api } from "../utils/api";
import { useLogs } from "../hooks/useLogs";
import { ConfirmModal } from "./ConfirmModal";

const TABS = ["General", "Interfaces", "Inventory", "Tags", "Macros", "Raw JSON"];

const EMPTY_SNMP_DETAILS = {
  version: "2",
  bulk: "1",
  community: "",
  max_repetitions: "10",
};

function deepClone(value) {
  return JSON.parse(JSON.stringify(value));
}

function extractFirstResult(payload) {
  if (Array.isArray(payload?.result)) return payload.result[0] || null;
  if (payload?.result && typeof payload.result === "object") return payload.result;
  if (Array.isArray(payload?.response?.result)) return payload.response.result[0] || null;
  if (payload?.response?.result && typeof payload.response.result === "object") return payload.response.result;
  return null;
}

function extractResultList(payload) {
  if (Array.isArray(payload?.result)) return payload.result;
  if (Array.isArray(payload?.response?.result)) return payload.response.result;
  return [];
}

function normalizeHost(data) {
  return {
    ...data,
    interfaces: Array.isArray(data?.interfaces) ? data.interfaces : [],
    inventory: Array.isArray(data?.inventory) ? {} : (data?.inventory || {}),
    tags: Array.isArray(data?.tags) ? data.tags : [],
    groups: Array.isArray(data?.groups) ? data.groups : [],
    macros: Array.isArray(data?.macros) ? data.macros : [],
  };
}

function ensureSnmpDetails(iface) {
  return {
    ...EMPTY_SNMP_DETAILS,
    ...(iface?.details || {}),
  };
}

function isSnmpInterface(iface) {
  return String(iface?.type) === "2";
}

function resolveSnmpCommunity(iface, host) {
  const detailsCommunity = ensureSnmpDetails(iface).community;
  if (detailsCommunity) return detailsCommunity;
  const macro = (host?.macros || []).find(item => item.macro === "{$SNMP_COMMUNITY}");
  return macro?.value || "";
}

function buildInterfacePayload(iface) {
  const payload = {
    ip: iface.ip || "",
    dns: iface.dns || "",
    port: iface.port || "",
    type: String(iface.type || "1"),
    main: String(iface.main || "0"),
    useip: String(iface.useip || "1"),
  };

  if (isSnmpInterface(iface)) {
    const details = ensureSnmpDetails(iface);
    payload.details = {
      version: String(details.version || "2"),
      bulk: String(details.bulk || "1"),
      community: details.community || "",
      max_repetitions: String(details.max_repetitions || "10"),
    };
  }

  return payload;
}

function buildSnmpValidationCommand(iface, host) {
  if (!isSnmpInterface(iface)) return "";
  const address = String(iface.useip || "1") === "1" ? iface.ip : iface.dns;
  const community = resolveSnmpCommunity(iface, host);
  if (!address || !community) return "";
  return `snmpget -Oqv -On -t 3 -r 1 -v 2c -c '${community}' ${address}:${iface.port || "161"} 1.3.6.1.2.1.1.2.0`;
}

function normalizeNewInterface(newIface) {
  return {
    ...newIface,
    type: String(newIface.type),
    main: String(newIface.main),
    useip: String(newIface.useip),
    ...(String(newIface.type) === "2" ? { details: { ...ensureSnmpDetails(newIface) } } : {}),
  };
}

export function ZabbixEditor({ hostId, onDataReady }) {
  const { addLog } = useLogs();
  const onDataReadyRef = useRef(onDataReady);
  const [loading, setLoading] = useState(true);
  const [host, setHost] = useState(null);
  const [tab, setTab] = useState("General");
  const [confirm, setConfirm] = useState(null);
  const [saving, setSaving] = useState(false);
  const [newIface, setNewIface] = useState({
    type: 1,
    main: 1,
    useip: 1,
    ip: "",
    dns: "",
    port: "10050",
    details: { ...EMPTY_SNMP_DETAILS },
  });
  const [addingIface, setAddingIface] = useState(false);

  useEffect(() => {
    onDataReadyRef.current = onDataReady;
  }, [onDataReady]);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const [hostResponse, interfacesResponse] = await Promise.all([
        api.zabbixHost(hostId),
        api.zabbixInterfaces(hostId).catch(() => ({ result: [] })),
      ]);
      const hostData = extractFirstResult(hostResponse);
      const interfaces = extractResultList(interfacesResponse);
      const data = normalizeHost({
        ...(hostData || {}),
        interfaces: interfaces.length > 0 ? interfaces : hostData?.interfaces,
      });
      if (!data?.hostid) throw new Error("Empty Zabbix host payload");
      setHost(data);
      if (onDataReadyRef.current) onDataReadyRef.current(data);
      addLog("ok", `Loaded Zabbix host: ${data.host}`, hostResponse.request);
    } catch (e) {
      addLog("err", "Failed to load Zabbix host: " + e.message);
      setHost(null);
    }
    setLoading(false);
  }, [hostId, addLog]);

  useEffect(() => {
    load();
  }, [load]);

  function updateField(path, value) {
    setHost(prev => {
      const next = deepClone(prev);
      const parts = path.split(".");
      let obj = next;
      for (let i = 0; i < parts.length - 1; i += 1) obj = obj[parts[i]];
      obj[parts[parts.length - 1]] = value;
      return next;
    });
  }

  function updateInterfaceField(interfaceid, field, value) {
    setHost(prev => {
      const next = deepClone(prev);
      const iface = next.interfaces.find(item => item.interfaceid === interfaceid);
      if (!iface) return prev;
      iface[field] = value;
      if (field === "type") {
        if (String(value) === "2") {
          iface.details = ensureSnmpDetails(iface);
          if (!iface.port || iface.port === "10050") iface.port = "161";
        } else {
          delete iface.details;
        }
      }
      return next;
    });
  }

  function updateInterfaceDetail(interfaceid, field, value) {
    setHost(prev => {
      const next = deepClone(prev);
      const iface = next.interfaces.find(item => item.interfaceid === interfaceid);
      if (!iface) return prev;
      iface.details = ensureSnmpDetails(iface);
      iface.details[field] = value;
      return next;
    });
  }

  async function handleSave(payload) {
    setSaving(true);
    try {
      const r = await api.zabbixUpdateHost(hostId, payload);
      addLog("ok", `Zabbix host updated: ${host.host}`, r);
      setConfirm(null);
      await load();
    } catch (e) {
      addLog("err", "Zabbix update failed: " + e.message);
    }
    setSaving(false);
  }

  async function handleAddInterface(payload) {
    setSaving(true);
    try {
      const r = await api.zabbixCreateInterface({ ...payload, hostid: hostId });
      addLog("ok", "Interface created in Zabbix", r);
      setConfirm(null);
      setAddingIface(false);
      setNewIface({
        type: 1,
        main: 1,
        useip: 1,
        ip: "",
        dns: "",
        port: "10050",
        details: { ...EMPTY_SNMP_DETAILS },
      });
      await load();
    } catch (e) {
      addLog("err", "Failed to create interface: " + e.message);
    }
    setSaving(false);
  }

  async function handleDeleteInterface(ifaceId) {
    if (!window.confirm("Delete this interface?")) return;
    try {
      await api.zabbixDeleteInterface(ifaceId);
      addLog("ok", "Interface deleted");
      await load();
    } catch (e) {
      addLog("err", "Delete failed: " + e.message);
    }
  }

  async function toggleArchiveSelf() {
    try {
      if (host.archived) {
        await api.restoreDevice("zabbix", host.hostid);
        addLog("ok", `Zabbix host restored: ${host.host}`);
      } else {
        await api.archiveDevice("zabbix", host.hostid, { label: host.host });
        addLog("ok", `Zabbix host archived: ${host.host}`);
      }
      await load();
    } catch (e) {
      addLog("err", "Archive toggle failed: " + e.message);
    }
  }

  function buildSavePayload() {
    const payload = {
      host: host.host,
      name: host.name,
      status: host.status,
      description: host.description,
    };
    if (host.groups) payload.groups = host.groups.map(group => ({ groupid: group.groupid }));
    if (host.inventory) payload.inventory = host.inventory;
    if (host.tags) payload.tags = host.tags;
    if (host.macros) payload.macros = host.macros;
    return payload;
  }

  if (loading) return <div style={{ padding: 20, color: "var(--text3)" }}>Loading...</div>;
  if (!host) return <div style={{ padding: 20, color: "var(--error)" }}>Host not found</div>;

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
        <span className="tag tag-zabbix">Z</span>
        <span style={{ fontFamily: "var(--font-mono)", fontWeight: 600, fontSize: 14 }}>{host.host}</span>
        <span style={{ color: "var(--text3)", fontSize: 12 }}>{host.name !== host.host ? `(${host.name})` : ""}</span>
        <span style={{ marginLeft: "auto" }}>
          <span className={`tag ${host.status === "0" ? "tag-ok" : "tag-err"}`}>
            {host.status === "0" ? "enabled" : "disabled"}
          </span>
        </span>
      </div>

      <div className="tabs" style={{ padding: "0 16px" }}>
        {TABS.map(item => (
          <button key={item} className={`tab-btn ${tab === item ? "active" : ""}`} onClick={() => setTab(item)}>
            {item}
          </button>
        ))}
      </div>

      <div style={{ flex: 1, overflow: "auto", padding: 16 }}>
        {tab === "General" && (
          <div>
            <div className="grid-2">
              <div className="field-row">
                <label>Host (technical name)</label>
                <input value={host.host || ""} onChange={e => updateField("host", e.target.value)} />
              </div>
              <div className="field-row">
                <label>Visible name</label>
                <input value={host.name || ""} onChange={e => updateField("name", e.target.value)} />
              </div>
            </div>
            <div className="field-row">
              <label>Description</label>
              <textarea
                value={host.description || ""}
                onChange={e => updateField("description", e.target.value)}
                style={{ height: 80, resize: "vertical" }}
              />
            </div>
            <div className="field-row">
              <label>Status</label>
              <select value={host.status || "0"} onChange={e => updateField("status", e.target.value)}>
                <option value="0">Enabled (0)</option>
                <option value="1">Disabled (1)</option>
              </select>
            </div>
            <div className="field-row">
              <label>Groups</label>
              <div style={{ display: "flex", flexWrap: "wrap", gap: 6 }}>
                {(host.groups || []).map(group => (
                  <span key={group.groupid} className="badge" style={{ background: "var(--bg4)", color: "var(--text2)" }}>
                    {group.name}
                  </span>
                ))}
              </div>
            </div>
          </div>
        )}

        {tab === "Interfaces" && (
          <div>
            <table style={{ marginBottom: 16 }}>
              <thead>
                <tr>
                  <th>Type</th>
                  <th>IP</th>
                  <th>DNS</th>
                  <th>Port</th>
                  <th>Main</th>
                  <th>Actions</th>
                </tr>
              </thead>
              <tbody>
                {(host.interfaces || []).map(iface => (
                  <tr key={iface.interfaceid}>
                    <td>
                      <select value={String(iface.type)} onChange={e => updateInterfaceField(iface.interfaceid, "type", e.target.value)} style={{ width: "auto" }}>
                        <option value="1">Agent</option>
                        <option value="2">SNMP</option>
                        <option value="3">IPMI</option>
                        <option value="4">JMX</option>
                      </select>
                    </td>
                    <td><input value={iface.ip || ""} onChange={e => updateInterfaceField(iface.interfaceid, "ip", e.target.value)} /></td>
                    <td><input value={iface.dns || ""} onChange={e => updateInterfaceField(iface.interfaceid, "dns", e.target.value)} /></td>
                    <td><input value={iface.port || ""} style={{ width: 80 }} onChange={e => updateInterfaceField(iface.interfaceid, "port", e.target.value)} /></td>
                    <td style={{ textAlign: "center" }}>{String(iface.main) === "1" ? "Yes" : ""}</td>
                    <td>
                      <div className="flex-gap">
                        <button
                          className="btn-secondary"
                          style={{ padding: "2px 8px", fontSize: 10 }}
                          onClick={() => setConfirm({
                            title: "Update Interface",
                            payload: buildInterfacePayload(iface),
                            onConfirm: async payload => {
                              setSaving(true);
                              try {
                                await api.zabbixUpdateInterface(iface.interfaceid, payload);
                                addLog("ok", "Interface updated");
                                await load();
                              } catch (e) {
                                addLog("err", e.message);
                              }
                              setSaving(false);
                              setConfirm(null);
                            }
                          })}
                        >
                          Save
                        </button>
                        <button className="btn-danger" style={{ padding: "2px 8px", fontSize: 10 }} onClick={() => handleDeleteInterface(iface.interfaceid)}>
                          X
                        </button>
                      </div>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>

            {(host.interfaces || []).map(iface => (
              <div key={`${iface.interfaceid}-details`} className="section" style={{ padding: 12, marginBottom: 12 }}>
                <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 10 }}>
                  <div style={{ fontFamily: "var(--font-mono)", fontSize: 12 }}>
                    Interface {iface.interfaceid} · {isSnmpInterface(iface) ? "SNMP" : "Non-SNMP"}
                  </div>
                  <div className="badge" style={{ background: "var(--bg4)", color: "var(--text2)" }}>
                    {String(iface.useip || "1") === "1" ? "Uses IP" : "Uses DNS"}
                  </div>
                </div>

                <div className="grid-2" style={{ marginBottom: 8 }}>
                  <div className="field-row">
                    <label>Main Interface</label>
                    <select value={String(iface.main || "0")} onChange={e => updateInterfaceField(iface.interfaceid, "main", e.target.value)}>
                      <option value="1">Yes</option>
                      <option value="0">No</option>
                    </select>
                  </div>
                  <div className="field-row">
                    <label>Address Mode</label>
                    <select value={String(iface.useip || "1")} onChange={e => updateInterfaceField(iface.interfaceid, "useip", e.target.value)}>
                      <option value="1">Use IP</option>
                      <option value="0">Use DNS</option>
                    </select>
                  </div>
                </div>

                {isSnmpInterface(iface) && (
                  <>
                    <div className="grid-2" style={{ marginBottom: 8 }}>
                      <div className="field-row">
                        <label>SNMP Version</label>
                        <select value={ensureSnmpDetails(iface).version} onChange={e => updateInterfaceDetail(iface.interfaceid, "version", e.target.value)}>
                          <option value="1">v1</option>
                          <option value="2">v2c</option>
                          <option value="3">v3</option>
                        </select>
                      </div>
                    <div className="field-row">
                      <label>Community</label>
                      <input value={ensureSnmpDetails(iface).community} onChange={e => updateInterfaceDetail(iface.interfaceid, "community", e.target.value)} />
                    </div>
                    <div className="field-row">
                      <label>Resolved Community</label>
                      <input value={resolveSnmpCommunity(iface, host)} readOnly />
                    </div>
                    <div className="field-row">
                      <label>Bulk Requests</label>
                      <select value={ensureSnmpDetails(iface).bulk} onChange={e => updateInterfaceDetail(iface.interfaceid, "bulk", e.target.value)}>
                          <option value="1">Enabled</option>
                          <option value="0">Disabled</option>
                        </select>
                      </div>
                      <div className="field-row">
                        <label>Max Repetitions</label>
                        <input value={ensureSnmpDetails(iface).max_repetitions} onChange={e => updateInterfaceDetail(iface.interfaceid, "max_repetitions", e.target.value)} />
                      </div>
                    </div>

                    <div className="field-row">
                      <label>Validation Command</label>
                      <textarea
                        readOnly
                        value={buildSnmpValidationCommand(iface, host) || "Complete the address and community to build the snmpget command."}
                        style={{ height: 74, resize: "vertical", fontFamily: "var(--font-mono)" }}
                      />
                    </div>
                  </>
                )}
              </div>
            ))}

            {!addingIface ? (
              <button className="btn-secondary" onClick={() => setAddingIface(true)}>+ Add Interface</button>
            ) : (
              <div className="section" style={{ padding: 12 }}>
                <div className="grid-2" style={{ marginBottom: 8 }}>
                  <div className="field-row">
                    <label>Type</label>
                    <select
                      value={newIface.type}
                      onChange={e => setNewIface(prev => ({
                        ...prev,
                        type: parseInt(e.target.value, 10),
                        port: parseInt(e.target.value, 10) === 2 ? "161" : prev.port === "161" ? "10050" : prev.port,
                      }))}
                    >
                      <option value={1}>Agent</option>
                      <option value={2}>SNMP</option>
                      <option value={3}>IPMI</option>
                      <option value={4}>JMX</option>
                    </select>
                  </div>
                  <div className="field-row">
                    <label>Address Mode</label>
                    <select value={newIface.useip} onChange={e => setNewIface(prev => ({ ...prev, useip: parseInt(e.target.value, 10) }))}>
                      <option value={1}>Use IP</option>
                      <option value={0}>Use DNS</option>
                    </select>
                  </div>
                  <div className="field-row">
                    <label>Port</label>
                    <input value={newIface.port} onChange={e => setNewIface(prev => ({ ...prev, port: e.target.value }))} />
                  </div>
                  <div className="field-row">
                    <label>Main Interface</label>
                    <select value={newIface.main} onChange={e => setNewIface(prev => ({ ...prev, main: parseInt(e.target.value, 10) }))}>
                      <option value={1}>Yes</option>
                      <option value={0}>No</option>
                    </select>
                  </div>
                  <div className="field-row">
                    <label>IP</label>
                    <input value={newIface.ip} onChange={e => setNewIface(prev => ({ ...prev, ip: e.target.value }))} />
                  </div>
                  <div className="field-row">
                    <label>DNS</label>
                    <input value={newIface.dns} onChange={e => setNewIface(prev => ({ ...prev, dns: e.target.value }))} />
                  </div>
                </div>

                {newIface.type === 2 && (
                  <div className="grid-2" style={{ marginBottom: 8 }}>
                    <div className="field-row">
                      <label>SNMP Version</label>
                      <select value={newIface.details.version} onChange={e => setNewIface(prev => ({ ...prev, details: { ...prev.details, version: e.target.value } }))}>
                        <option value="1">v1</option>
                        <option value="2">v2c</option>
                        <option value="3">v3</option>
                      </select>
                    </div>
                    <div className="field-row">
                      <label>Community</label>
                      <input value={newIface.details.community} onChange={e => setNewIface(prev => ({ ...prev, details: { ...prev.details, community: e.target.value } }))} />
                    </div>
                    <div className="field-row">
                      <label>Bulk Requests</label>
                      <select value={newIface.details.bulk} onChange={e => setNewIface(prev => ({ ...prev, details: { ...prev.details, bulk: e.target.value } }))}>
                        <option value="1">Enabled</option>
                        <option value="0">Disabled</option>
                      </select>
                    </div>
                    <div className="field-row">
                      <label>Max Repetitions</label>
                      <input value={newIface.details.max_repetitions} onChange={e => setNewIface(prev => ({ ...prev, details: { ...prev.details, max_repetitions: e.target.value } }))} />
                    </div>
                  </div>
                )}

                <div className="flex-gap">
                  <button
                    className="btn-primary"
                    onClick={() => setConfirm({
                      title: "Create Interface",
                      payload: normalizeNewInterface(newIface),
                      onConfirm: handleAddInterface,
                    })}
                  >
                    Preview & Create
                  </button>
                  <button className="btn-secondary" onClick={() => setAddingIface(false)}>Cancel</button>
                </div>
              </div>
            )}
          </div>
        )}

        {tab === "Inventory" && (
          <div>
            <div className="notice notice-info" style={{ marginBottom: 12 }}>
              Edit inventory fields below. All changes are staged - click "Preview & Save" to apply.
            </div>
            {host.inventory && typeof host.inventory === "object" ? (
              <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 8 }}>
                {Object.entries(host.inventory).filter(([key]) => key !== "hostid").map(([key, value]) => (
                  <div className="field-row" key={key}>
                    <label>{key}</label>
                    <input value={value || ""} onChange={e => updateField(`inventory.${key}`, e.target.value)} />
                  </div>
                ))}
              </div>
            ) : (
              <div style={{ color: "var(--text3)", fontFamily: "var(--font-mono)" }}>No inventory data</div>
            )}
          </div>
        )}

        {tab === "Tags" && (
          <div>
            <table style={{ marginBottom: 12 }}>
              <thead>
                <tr>
                  <th>Tag</th>
                  <th>Value</th>
                </tr>
              </thead>
              <tbody>
                {(host.tags || []).map((tag, index) => (
                  <tr key={index}>
                    <td>
                      <input
                        value={tag.tag || ""}
                        onChange={e => setHost(prev => {
                          const next = deepClone(prev);
                          next.tags[index].tag = e.target.value;
                          return next;
                        })}
                      />
                    </td>
                    <td>
                      <input
                        value={tag.value || ""}
                        onChange={e => setHost(prev => {
                          const next = deepClone(prev);
                          next.tags[index].value = e.target.value;
                          return next;
                        })}
                      />
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
            <button className="btn-secondary" onClick={() => setHost(prev => ({ ...prev, tags: [...(prev.tags || []), { tag: "", value: "" }] }))}>
              + Add Tag
            </button>
          </div>
        )}

        {tab === "Macros" && (
          <div>
            <table style={{ marginBottom: 12 }}>
              <thead>
                <tr>
                  <th>Macro</th>
                  <th>Value</th>
                  <th>Type</th>
                </tr>
              </thead>
              <tbody>
                {(host.macros || []).map((macro, index) => (
                  <tr key={macro.hostmacroid || `${macro.macro}-${index}`}>
                    <td>
                      <input
                        value={macro.macro || ""}
                        onChange={e => setHost(prev => {
                          const next = deepClone(prev);
                          next.macros[index].macro = e.target.value;
                          return next;
                        })}
                      />
                    </td>
                    <td>
                      <input
                        value={macro.value || ""}
                        onChange={e => setHost(prev => {
                          const next = deepClone(prev);
                          next.macros[index].value = e.target.value;
                          return next;
                        })}
                      />
                    </td>
                    <td style={{ color: "var(--text3)", fontFamily: "var(--font-mono)" }}>{macro.type ?? "0"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
            <button
              className="btn-secondary"
              onClick={() => setHost(prev => ({
                ...prev,
                macros: [...(prev.macros || []), { macro: "", value: "", type: "0" }],
              }))}
            >
              + Add Macro
            </button>
          </div>
        )}

        {tab === "Raw JSON" && (
          <div className="json-preview" style={{ height: "100%", minHeight: 400 }}>
            {JSON.stringify(host, null, 2)}
          </div>
        )}
      </div>

      {tab !== "Raw JSON" && tab !== "Interfaces" && (
        <div style={{ padding: "12px 16px", borderTop: "1px solid var(--border)", display: "flex", gap: 8 }}>
          <button
            className="btn-primary"
            onClick={() => setConfirm({
              title: `Update Zabbix Host: ${host.host}`,
              payload: buildSavePayload(),
              onConfirm: handleSave,
            })}
          >
            Preview & Save to Zabbix
          </button>
          <button className="btn-secondary" onClick={load}>Reload</button>
          <button className="btn-secondary" onClick={toggleArchiveSelf}>{host.archived ? "Restore" : "Archive"}</button>
        </div>
      )}
    </div>
  );
}
