import { useState, useEffect, useCallback, useRef } from "react";
import { api } from "../utils/api";
import { useLogs } from "../hooks/useLogs";
import { ConfirmModal } from "./ConfirmModal";

const EDIT_TABS = ["General", "Interfaces", "Inventory", "Tags", "Macros", "Raw JSON"];
const CREATE_TABS = ["General", "Interfaces", "Macros", "Raw JSON"];

const EMPTY_SNMP_DETAILS = {
  version: "2",
  bulk: "1",
  community: "",
  max_repetitions: "10",
};

const EMPTY_NEW_INTERFACE = {
  type: 1,
  main: 1,
  useip: 1,
  ip: "",
  dns: "",
  port: "10050",
  details: { ...EMPTY_SNMP_DETAILS },
};

const EMPTY_CREATE_HOST = {
  host: "",
  name: "",
  description: "",
  status: "0",
  groups: [],
  inventory: {},
  tags: [],
  macros: [],
  interfaces: [
    {
      type: "2",
      main: "1",
      useip: "1",
      ip: "",
      dns: "",
      port: "161",
      details: { ...EMPTY_SNMP_DETAILS },
    },
  ],
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

function safeScalar(value) {
  if (value === null || value === undefined) return "";
  if (typeof value === "string") return value;
  if (typeof value === "number" || typeof value === "boolean") return String(value);
  return JSON.stringify(value);
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

function hostMacroValue(host, macroName) {
  const target = String(macroName || "").trim();
  if (!target) return "";
  const macro = (host?.macros || []).find(item => String(item.macro || "").trim() === target);
  return macro?.value || "";
}

function resolveSnmpCommunity(iface, host) {
  const detailsCommunity = String(ensureSnmpDetails(iface).community || "").trim();
  if (/^\{\$[^}]+\}$/.test(detailsCommunity)) {
    return hostMacroValue(host, detailsCommunity) || detailsCommunity;
  }
  if (detailsCommunity) return detailsCommunity;
  return hostMacroValue(host, "{$SNMP_COMMUNITY}");
}

function buildInterfacePayload(iface, host) {
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
      community: resolveSnmpCommunity(iface, host) || "",
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

function emptyCreateHost() {
  return deepClone(EMPTY_CREATE_HOST);
}

export function ZabbixEditor({ hostId, onDataReady }) {
  const { addLog } = useLogs();
  const onDataReadyRef = useRef(onDataReady);
  const [activeHostId, setActiveHostId] = useState(hostId);
  const [loading, setLoading] = useState(true);
  const [host, setHost] = useState(null);
  const [groups, setGroups] = useState([]);
  const [tab, setTab] = useState("General");
  const [confirm, setConfirm] = useState(null);
  const [saving, setSaving] = useState(false);
  const [newIface, setNewIface] = useState({ ...EMPTY_NEW_INTERFACE, details: { ...EMPTY_SNMP_DETAILS } });
  const [addingIface, setAddingIface] = useState(false);

  useEffect(() => {
    onDataReadyRef.current = onDataReady;
  }, [onDataReady]);

  useEffect(() => {
    setActiveHostId(hostId);
    setTab("General");
    setAddingIface(false);
    setNewIface({ ...EMPTY_NEW_INTERFACE, details: { ...EMPTY_SNMP_DETAILS } });
  }, [hostId]);

  const isCreateMode = activeHostId === "new";
  const tabs = isCreateMode ? CREATE_TABS : EDIT_TABS;

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const groupsResponse = await api.zabbixGroups().catch(() => ({ result: [] }));
      setGroups(extractResultList(groupsResponse));

      if (isCreateMode) {
        const draft = emptyCreateHost();
        setHost(draft);
        if (onDataReadyRef.current) onDataReadyRef.current(draft);
        setLoading(false);
        return;
      }

      const [hostResponse, interfacesResponse] = await Promise.all([
        api.zabbixHost(activeHostId),
        api.zabbixInterfaces(activeHostId).catch(() => ({ result: [] })),
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
      setHost(isCreateMode ? emptyCreateHost() : null);
    }
    setLoading(false);
  }, [activeHostId, addLog, isCreateMode]);

  useEffect(() => {
    load();
  }, [load]);

  function updateField(path, value) {
    setHost(prev => {
      const next = deepClone(prev);
      const parts = path.split(".");
      let obj = next;
      for (let i = 0; i < parts.length - 1; i += 1) {
        if (!obj[parts[i]]) obj[parts[i]] = {};
        obj = obj[parts[i]];
      }
      obj[parts[parts.length - 1]] = value;
      return next;
    });
  }

  function updateHostList(field, updater) {
    setHost(prev => {
      const next = deepClone(prev);
      next[field] = updater(next[field] || []);
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

  function updateDraftInterface(index, field, value) {
    setHost(prev => {
      const next = deepClone(prev);
      const iface = next.interfaces[index];
      if (!iface) return prev;
      iface[field] = value;
      if (field === "type") {
        if (String(value) === "2") {
          iface.details = ensureSnmpDetails(iface);
          if (!iface.port || iface.port === "10050") iface.port = "161";
        } else {
          delete iface.details;
          if (iface.port === "161") iface.port = "10050";
        }
      }
      return next;
    });
  }

  function updateDraftInterfaceDetail(index, field, value) {
    setHost(prev => {
      const next = deepClone(prev);
      const iface = next.interfaces[index];
      if (!iface) return prev;
      iface.details = ensureSnmpDetails(iface);
      iface.details[field] = value;
      return next;
    });
  }

  function removeDraftInterface(index) {
    setHost(prev => ({
      ...prev,
      interfaces: (prev.interfaces || []).filter((_, currentIndex) => currentIndex !== index),
    }));
  }

  function toggleGroup(group) {
    setHost(prev => {
      const exists = (prev.groups || []).some(item => String(item.groupid || item.id) === String(group.groupid));
      return {
        ...prev,
        groups: exists
          ? (prev.groups || []).filter(item => String(item.groupid || item.id) !== String(group.groupid))
          : [...(prev.groups || []), { groupid: String(group.groupid), name: group.name }],
      };
    });
  }

  async function handleSave(payload) {
    setSaving(true);
    try {
      const r = await api.zabbixUpdateHost(activeHostId, payload);
      addLog("ok", `Zabbix host updated: ${host.host}`, r);
      setConfirm(null);
      await load();
    } catch (e) {
      addLog("err", "Zabbix update failed: " + e.message);
    }
    setSaving(false);
  }

  async function handleCreateHost(payload) {
    setSaving(true);
    try {
      const r = await api.zabbixCreateHost(payload);
      const createdId = r.result?.hostids?.[0] || r.response?.result?.hostids?.[0] || null;
      addLog("ok", `Zabbix host created: ${payload.host}`, r);
      setConfirm(null);
      if (createdId) {
        setActiveHostId(createdId);
      } else {
        await load();
      }
    } catch (e) {
      addLog("err", "Zabbix create failed: " + e.message);
    }
    setSaving(false);
  }

  async function handleAddInterface(payload) {
    if (isCreateMode) {
      setHost(prev => ({
        ...prev,
        interfaces: [...(prev.interfaces || []), normalizeNewInterface(payload)],
      }));
      setAddingIface(false);
      setNewIface({ ...EMPTY_NEW_INTERFACE, details: { ...EMPTY_SNMP_DETAILS } });
      setConfirm(null);
      return;
    }

    setSaving(true);
    try {
      const r = await api.zabbixCreateInterface({ ...payload, hostid: activeHostId });
      addLog("ok", "Interface created in Zabbix", r);
      setConfirm(null);
      setAddingIface(false);
      setNewIface({ ...EMPTY_NEW_INTERFACE, details: { ...EMPTY_SNMP_DETAILS } });
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
    if (Array.isArray(host.groups) && host.groups.length > 0) payload.groups = host.groups.map(group => ({ groupid: group.groupid }));
    if (host.inventory && Object.keys(host.inventory).length > 0) payload.inventory = host.inventory;
    if (Array.isArray(host.tags) && host.tags.length > 0) payload.tags = host.tags;
    if (Array.isArray(host.macros) && host.macros.length > 0) payload.macros = host.macros;
    return payload;
  }

  function buildCreatePayload() {
    const payload = {
      host: host.host,
      name: host.name || host.host,
      status: String(host.status || "0"),
      description: host.description || "",
      groups: (host.groups || []).map(group => ({ groupid: String(group.groupid || group.id) })),
      interfaces: (host.interfaces || []).map(iface => buildInterfacePayload(iface, host)),
    };

    const macros = (host.macros || []).filter(item => item.macro || item.value);
    if (macros.length > 0) payload.macros = macros;
    const tags = (host.tags || []).filter(item => item.tag || item.value);
    if (tags.length > 0) payload.tags = tags;
    if (host.inventory && Object.keys(host.inventory).length > 0) payload.inventory = host.inventory;
    return payload;
  }

  function validateCreateHost() {
    if (!host.host?.trim()) return "Host is required";
    if (!Array.isArray(host.groups) || host.groups.length === 0) return "Select at least one Zabbix group";
    if (!Array.isArray(host.interfaces) || host.interfaces.length === 0) return "Add at least one interface";
    if (!(host.interfaces || []).some(isSnmpInterface)) return "Add at least one SNMP interface";

    for (const iface of host.interfaces || []) {
      const usesIp = String(iface.useip || "1") === "1";
      if (usesIp && !iface.ip?.trim()) return "Each interface using IP must define an IP address";
      if (!usesIp && !iface.dns?.trim()) return "Each interface using DNS must define a DNS name";
      if (isSnmpInterface(iface) && !resolveSnmpCommunity(iface, host)) return "Each SNMP interface must define a community or use a Zabbix user macro like {$RO}";
    }

    return "";
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
        <span style={{ fontFamily: "var(--font-mono)", fontWeight: 600, fontSize: 14 }}>{isCreateMode ? "New Zabbix SNMP Host" : host.host}</span>
        {!isCreateMode && <span style={{ color: "var(--text3)", fontSize: 12 }}>{host.name !== host.host ? `(${host.name})` : ""}</span>}
        {!isCreateMode && (
          <span style={{ marginLeft: "auto" }}>
            <span className={`tag ${host.status === "0" ? "tag-ok" : "tag-err"}`}>
              {host.status === "0" ? "enabled" : "disabled"}
            </span>
          </span>
        )}
      </div>

      <div className="tabs" style={{ padding: "0 16px" }}>
        {tabs.map(item => (
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
              <textarea value={host.description || ""} onChange={e => updateField("description", e.target.value)} style={{ height: 80, resize: "vertical" }} />
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
              <div style={{ display: "flex", flexWrap: "wrap", gap: 8 }}>
                {groups.map(group => {
                  const checked = (host.groups || []).some(item => String(item.groupid || item.id) === String(group.groupid));
                  return (
                    <label key={group.groupid} style={{ display: "flex", alignItems: "center", gap: 6, padding: "6px 10px", border: checked ? "1px solid var(--accent)" : "1px solid var(--border)", borderRadius: "var(--radius)", cursor: "pointer", background: checked ? "rgba(0,212,170,0.08)" : "transparent" }}>
                      <input type="checkbox" checked={checked} onChange={() => toggleGroup(group)} />
                      <span>{group.name}</span>
                    </label>
                  );
                })}
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
                {(host.interfaces || []).map((iface, index) => (
                  <tr key={iface.interfaceid || `draft-${index}`}>
                    <td>
                      <select
                        value={String(iface.type)}
                        onChange={e => isCreateMode ? updateDraftInterface(index, "type", e.target.value) : updateInterfaceField(iface.interfaceid, "type", e.target.value)}
                        style={{ width: "auto" }}
                      >
                        <option value="1">Agent</option>
                        <option value="2">SNMP</option>
                        <option value="3">IPMI</option>
                        <option value="4">JMX</option>
                      </select>
                    </td>
                    <td><input value={iface.ip || ""} onChange={e => isCreateMode ? updateDraftInterface(index, "ip", e.target.value) : updateInterfaceField(iface.interfaceid, "ip", e.target.value)} /></td>
                    <td><input value={iface.dns || ""} onChange={e => isCreateMode ? updateDraftInterface(index, "dns", e.target.value) : updateInterfaceField(iface.interfaceid, "dns", e.target.value)} /></td>
                    <td><input value={iface.port || ""} style={{ width: 80 }} onChange={e => isCreateMode ? updateDraftInterface(index, "port", e.target.value) : updateInterfaceField(iface.interfaceid, "port", e.target.value)} /></td>
                    <td style={{ textAlign: "center" }}>{String(iface.main) === "1" ? "Yes" : ""}</td>
                    <td>
                      {isCreateMode ? (
                        <button className="btn-danger" style={{ padding: "2px 8px", fontSize: 10 }} onClick={() => removeDraftInterface(index)}>
                          Remove
                        </button>
                      ) : (
                        <div className="flex-gap">
                          <button
                            className="btn-secondary"
                            style={{ padding: "2px 8px", fontSize: 10 }}
                            onClick={() => setConfirm({
                              title: "Update Interface",
                              payload: buildInterfacePayload(iface, host),
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
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>

            {(host.interfaces || []).map((iface, index) => {
              const key = iface.interfaceid || `draft-${index}`;
              return (
                <div key={`${key}-details`} className="section" style={{ padding: 12, marginBottom: 12 }}>
                  <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 10 }}>
                    <div style={{ fontFamily: "var(--font-mono)", fontSize: 12 }}>
                      Interface {iface.interfaceid || `draft-${index + 1}`} · {isSnmpInterface(iface) ? "SNMP" : "Non-SNMP"}
                    </div>
                    <div className="badge" style={{ background: "var(--bg4)", color: "var(--text2)" }}>
                      {String(iface.useip || "1") === "1" ? "Uses IP" : "Uses DNS"}
                    </div>
                  </div>

                  <div className="grid-2" style={{ marginBottom: 8 }}>
                    <div className="field-row">
                      <label>Main Interface</label>
                      <select value={String(iface.main || "0")} onChange={e => isCreateMode ? updateDraftInterface(index, "main", e.target.value) : updateInterfaceField(iface.interfaceid, "main", e.target.value)}>
                        <option value="1">Yes</option>
                        <option value="0">No</option>
                      </select>
                    </div>
                    <div className="field-row">
                      <label>Address Mode</label>
                      <select value={String(iface.useip || "1")} onChange={e => isCreateMode ? updateDraftInterface(index, "useip", e.target.value) : updateInterfaceField(iface.interfaceid, "useip", e.target.value)}>
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
                          <select value={ensureSnmpDetails(iface).version} onChange={e => isCreateMode ? updateDraftInterfaceDetail(index, "version", e.target.value) : updateInterfaceDetail(iface.interfaceid, "version", e.target.value)}>
                            <option value="1">v1</option>
                            <option value="2">v2c</option>
                            <option value="3">v3</option>
                          </select>
                        </div>
                        <div className="field-row">
                          <label>Community</label>
                          <input value={ensureSnmpDetails(iface).community} onChange={e => isCreateMode ? updateDraftInterfaceDetail(index, "community", e.target.value) : updateInterfaceDetail(iface.interfaceid, "community", e.target.value)} />
                        </div>
                        <div className="field-row">
                          <label>Resolved Community</label>
                          <input value={resolveSnmpCommunity(iface, host)} readOnly />
                        </div>
                        <div className="field-row">
                          <label>Bulk Requests</label>
                          <select value={ensureSnmpDetails(iface).bulk} onChange={e => isCreateMode ? updateDraftInterfaceDetail(index, "bulk", e.target.value) : updateInterfaceDetail(iface.interfaceid, "bulk", e.target.value)}>
                            <option value="1">Enabled</option>
                            <option value="0">Disabled</option>
                          </select>
                        </div>
                        <div className="field-row">
                          <label>Max Repetitions</label>
                          <input value={ensureSnmpDetails(iface).max_repetitions} onChange={e => isCreateMode ? updateDraftInterfaceDetail(index, "max_repetitions", e.target.value) : updateInterfaceDetail(iface.interfaceid, "max_repetitions", e.target.value)} />
                        </div>
                      </div>

                      <div className="field-row">
                        <label>Validation Command</label>
                        <textarea readOnly value={buildSnmpValidationCommand(iface, host) || "Complete the address and community to build the snmpget command."} style={{ height: 74, resize: "vertical", fontFamily: "var(--font-mono)" }} />
                      </div>
                    </>
                  )}
                </div>
              );
            })}

            {!addingIface ? (
              <button className="btn-secondary" onClick={() => setAddingIface(true)}>{isCreateMode ? "+ Add Draft Interface" : "+ Add Interface"}</button>
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
                  <button className="btn-primary" onClick={() => setConfirm({ title: isCreateMode ? "Stage Interface" : "Create Interface", payload: normalizeNewInterface(newIface), onConfirm: handleAddInterface })}>
                    {isCreateMode ? "Preview & Add to Draft" : "Preview & Create"}
                  </button>
                  <button className="btn-secondary" onClick={() => setAddingIface(false)}>Cancel</button>
                </div>
              </div>
            )}
          </div>
        )}

        {tab === "Inventory" && !isCreateMode && (
          <div>
            <div className="notice notice-info" style={{ marginBottom: 12 }}>
              Edit inventory fields below. All changes are staged - click "Preview & Save" to apply.
            </div>
            {host.inventory && typeof host.inventory === "object" ? (
              <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 8 }}>
                {Object.entries(host.inventory).filter(([key]) => key !== "hostid").map(([key, value]) => (
                  <div className="field-row" key={key}>
                    <label>{key}</label>
                    <input value={safeScalar(value)} onChange={e => updateField(`inventory.${key}`, e.target.value)} />
                  </div>
                ))}
              </div>
            ) : (
              <div style={{ color: "var(--text3)", fontFamily: "var(--font-mono)" }}>No inventory data</div>
            )}
          </div>
        )}

        {tab === "Tags" && !isCreateMode && (
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
                        onChange={e => updateHostList("tags", items => items.map((item, itemIndex) => itemIndex === index ? { ...item, tag: e.target.value } : item))}
                      />
                    </td>
                    <td>
                      <input
                        value={safeScalar(tag.value)}
                        onChange={e => updateHostList("tags", items => items.map((item, itemIndex) => itemIndex === index ? { ...item, value: e.target.value } : item))}
                      />
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
            <button className="btn-secondary" onClick={() => updateHostList("tags", items => [...items, { tag: "", value: "" }])}>
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
                        onChange={e => updateHostList("macros", items => items.map((item, itemIndex) => itemIndex === index ? { ...item, macro: e.target.value } : item))}
                      />
                    </td>
                    <td>
                      <input
                        value={safeScalar(macro.value)}
                        onChange={e => updateHostList("macros", items => items.map((item, itemIndex) => itemIndex === index ? { ...item, value: e.target.value } : item))}
                      />
                    </td>
                    <td style={{ color: "var(--text3)", fontFamily: "var(--font-mono)" }}>{macro.type ?? "0"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
            <button className="btn-secondary" onClick={() => updateHostList("macros", items => [...items, { macro: "", value: "", type: "0" }])}>
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

      {tab !== "Raw JSON" && (isCreateMode || tab !== "Interfaces") && (
        <div style={{ padding: "12px 16px", borderTop: "1px solid var(--border)", display: "flex", gap: 8, flexWrap: "wrap" }}>
          <button
            className="btn-primary"
            onClick={() => {
              if (isCreateMode) {
                const validationError = validateCreateHost();
                if (validationError) {
                  addLog("err", validationError);
                  return;
                }
                setConfirm({ title: `Create Zabbix Host: ${host.host}`, payload: buildCreatePayload(), onConfirm: handleCreateHost });
                return;
              }
              setConfirm({ title: `Update Zabbix Host: ${host.host}`, payload: buildSavePayload(), onConfirm: handleSave });
            }}
          >
            {isCreateMode ? "Preview & Create in Zabbix" : "Preview & Save to Zabbix"}
          </button>
          <button className="btn-secondary" onClick={load}>{isCreateMode ? "Reset Draft" : "Reload"}</button>
          {!isCreateMode && <button className="btn-secondary" onClick={toggleArchiveSelf}>{host.archived ? "Restore" : "Archive"}</button>}
        </div>
      )}
    </div>
  );
}
