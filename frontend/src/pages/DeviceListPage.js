import { useEffect, useMemo, useState } from "react";
import { api } from "../utils/api";
import { useLogs } from "../hooks/useLogs";

function matches(item, text) {
  return text.trim() === "" || item.toLowerCase().includes(text.trim().toLowerCase());
}

function normalizeName(value) {
  return String(value || "").trim().toLowerCase().replace(/[^a-z0-9]+/g, "");
}

function normalizeIp(value) {
  const raw = String(value || "").trim();
  if (!raw) return "";
  return raw.split("/")[0].trim();
}

function zabbixMainIp(host) {
  return (host.interfaces || []).find(item => item.main === "1")?.ip || host.interfaces?.[0]?.ip || "";
}

function netboxPrimaryIp(device) {
  return device.primary_ip4?.address || device.primary_ip?.address || "";
}

function buildSavedCorrelationIndex(groups) {
  const itemMap = { zabbix: {}, netbox: {}, observium: {} };
  for (const group of groups || []) {
    const items = group.items || {};
    for (const [source, item] of Object.entries(items)) {
      itemMap[source][String(item.id)] = {
        kind: "saved",
        groupId: group.id,
        label: group.label || Object.values(items).map(entry => entry.label || entry.id).join(" | "),
        items,
      };
    }
  }
  return itemMap;
}

function buildAutoCorrelationIndex(zHosts, nbDevices, obsDevices, savedMap) {
  const groups = [];
  const itemMap = { zabbix: {}, netbox: {}, observium: {} };
  const ipBuckets = new Map();

  function addToBucket(source, id, label, ip, name) {
    const ipKey = normalizeIp(ip);
    if (!ipKey || savedMap[source]?.[String(id)]) return;
    if (!ipBuckets.has(ipKey)) ipBuckets.set(ipKey, { zabbix: [], netbox: [], observium: [] });
    ipBuckets.get(ipKey)[source].push({
      id: String(id),
      label,
      ipKey,
      nameKey: normalizeName(name),
    });
  }

  zHosts.forEach(host => addToBucket("zabbix", host.hostid, host.host || host.name || String(host.hostid), zabbixMainIp(host), host.host || host.name));
  nbDevices.forEach(device => addToBucket("netbox", device.id, device.name || String(device.id), netboxPrimaryIp(device), device.name));
  obsDevices.forEach(device => addToBucket("observium", device.device_id, device.hostname || device.sysName || String(device.device_id), device.ip, device.hostname || device.sysName || device.label));

  for (const [ipKey, bucket] of ipBuckets.entries()) {
    if (bucket.zabbix.length > 1 || bucket.netbox.length > 1 || bucket.observium.length > 1) continue;
    const z = bucket.zabbix[0] || null;
    const n = bucket.netbox[0] || null;
    const o = bucket.observium[0] || null;
    const reasons = [];

    if (z && n && z.nameKey && n.nameKey && z.nameKey === n.nameKey) {
      reasons.push("same name + ip");
    }
    if (o && (z || n)) {
      reasons.push("observium same ip");
    }
    if (!reasons.length) continue;

    const items = {};
    if (z) items.zabbix = { id: z.id, label: z.label };
    if (n) items.netbox = { id: n.id, label: n.label };
    if (o) items.observium = { id: o.id, label: o.label };
    const label = `Auto ${ipKey}`;
    const group = { kind: "auto", groupId: `auto:${ipKey}`, label, reasons, items };
    groups.push(group);
    for (const [source, item] of Object.entries(items)) {
      itemMap[source][String(item.id)] = group;
    }
  }

  return { groups, itemMap };
}

function labelWithMeta(label, meta) {
  if (!meta) return label;
  return meta.label || label;
}

function panelTag(type) {
  if (type === "zabbix") return <span className="tag tag-zabbix">ZABBIX</span>;
  if (type === "netbox") return <span className="tag tag-netbox">NETBOX</span>;
  return <span className="tag" style={{ background: "rgba(255,172,48,0.18)", color: "#ffac30", borderColor: "rgba(255,172,48,0.45)" }}>OBSERVIUM</span>;
}

function linkedPlatformBadges(sourceName, meta) {
  if (!meta) return null;
  if (meta.kind !== "saved") return <span className={`correlation-pill ${meta.kind}`}>auto</span>;
  const items = Object.keys(meta.items || {}).filter(item => item !== sourceName);
  if (!items.length) return null;
  const badgeStyle = (type) => {
    const base = {
      display: "inline-flex",
      alignItems: "center",
      justifyContent: "center",
      width: 16,
      height: 16,
      padding: 0,
      borderRadius: 2,
      fontSize: 8,
      fontWeight: 700,
      lineHeight: 1,
    };
    if (type === "zabbix") return { ...base, color: "#ff6b6b", border: "1px solid rgba(255,107,107,0.45)", background: "rgba(255,107,107,0.14)" };
    if (type === "netbox") return { ...base, color: "#2dd4ff", border: "1px solid rgba(45,212,255,0.45)", background: "rgba(45,212,255,0.14)" };
    return { ...base, color: "#ffac30", border: "1px solid rgba(255,172,48,0.45)", background: "rgba(255,172,48,0.16)" };
  };
  const badgeLabel = (type) => (type === "zabbix" ? "Z" : type === "netbox" ? "N" : "O");
  return (
    <span style={{ display: "inline-flex", alignItems: "center", gap: 4 }}>
      {items.map((item) => (
        <span key={item} className="correlation-pill saved" style={badgeStyle(item)}>{badgeLabel(item)}</span>
      ))}
    </span>
  );
}

function linkStyle() {
  return {
    color: "var(--text)",
    textDecoration: "underline",
    textDecorationColor: "rgba(0,153,255,0.45)",
    textUnderlineOffset: "2px",
  };
}

export function DeviceListPage({ onOpenDevice, active, uiPrefs }) {
  const { addLog } = useLogs();
  const [zHosts, setZHosts] = useState([]);
  const [nbDevices, setNbDevices] = useState([]);
  const [obsDevices, setObsDevices] = useState([]);
  const [correlations, setCorrelations] = useState([]);
  const [loading, setLoading] = useState({ zabbix: false, netbox: false, observium: false });
  const [filter, setFilter] = useState({ zabbix: "", netbox: "", observium: "" });
  const [archiveView, setArchiveView] = useState({ zabbix: "exclude", netbox: "exclude", observium: "exclude" });
  const [selectedZabbix, setSelectedZabbix] = useState({});
  const [selectedNetbox, setSelectedNetbox] = useState({});
  const [selectedObservium, setSelectedObservium] = useState({});
  const [source, setSource] = useState("all");
  const [downloadingDrawio, setDownloadingDrawio] = useState(false);

  async function loadCorrelations() {
    try {
      const response = await api.correlations();
      setCorrelations(response.result || []);
    } catch (error) {
      addLog("err", `Correlation index load failed: ${error.message}`);
    }
  }

  useEffect(() => {
    if (active && (zHosts.length || nbDevices.length || obsDevices.length)) {
      loadCorrelations();
    }
  }, [active, zHosts.length, nbDevices.length, obsDevices.length]);

  async function loadZabbix(mode = archiveView.zabbix) {
    setLoading(prev => ({ ...prev, zabbix: true }));
    try {
      const response = await api.zabbixHosts(500, mode);
      setZHosts(response.result || []);
      addLog("ok", `Loaded ${(response.result || []).length} Zabbix hosts (${mode})`);
    } catch (error) {
      addLog("err", "Zabbix load failed: " + error.message);
    }
    setLoading(prev => ({ ...prev, zabbix: false }));
  }

  async function loadNetbox(mode = archiveView.netbox) {
    setLoading(prev => ({ ...prev, netbox: true }));
    try {
      const response = await api.netboxDevices(500, mode);
      setNbDevices(response.result?.results || []);
      addLog("ok", `Loaded ${(response.result?.results || []).length} NetBox devices (${mode})`);
    } catch (error) {
      addLog("err", "NetBox load failed: " + error.message);
    }
    setLoading(prev => ({ ...prev, netbox: false }));
  }

  async function loadObservium(mode = archiveView.observium) {
    setLoading(prev => ({ ...prev, observium: true }));
    try {
      const response = await api.observiumDevices(500, "", mode);
      setObsDevices(response.result || []);
      addLog("ok", `Loaded ${(response.result || []).length} Observium devices (${mode})`);
    } catch (error) {
      addLog("err", "Observium load failed: " + error.message);
    }
    setLoading(prev => ({ ...prev, observium: false }));
  }

  function loadAll() {
    loadZabbix();
    loadNetbox();
    loadObservium();
    loadCorrelations();
  }

  async function downloadInventoryDrawio() {
    setDownloadingDrawio(true);
    try {
      await api.downloadInventoryDrawio(archiveView.netbox);
      addLog("ok", `Inventory draw.io downloaded using NetBox filter: ${archiveView.netbox}`);
    } catch (error) {
      addLog("err", `Inventory draw.io download failed: ${error.message}`);
    }
    setDownloadingDrawio(false);
  }

  async function toggleArchive(sourceName, item, archived) {
    try {
      if (archived) {
        await api.restoreDevice(sourceName, item.id);
      addLog("ok", `${sourceName} device restored: ${item.label}`);
      } else {
        await api.archiveDevice(sourceName, item.id, { label: item.label, details: item.details || {} });
        addLog("ok", `${sourceName} device archived: ${item.label}`);
      }
      if (sourceName === "zabbix") loadZabbix();
      if (sourceName === "netbox") loadNetbox();
      if (sourceName === "observium") loadObservium();
      loadCorrelations();
    } catch (error) {
      addLog("err", `Archive action failed: ${error.message}`);
    }
  }

  async function exportSelectedToObservium(hostids) {
    if (!hostids.length) {
      addLog("err", "Select at least one Zabbix host first");
      return;
    }
    try {
      const response = await api.exportZabbixToObservium({ hostids, run_discovery: false, run_poller: false, update_existing: true });
      const created = response.result.filter(item => item.status === "created").length;
      const updated = response.result.filter(item => item.status === "updated").length;
      const skipped = response.result.filter(item => item.status === "skipped").length;
      const errors = response.result.filter(item => item.status === "error").length;
      const detailLines = (response.result || [])
        .filter(item => item.status === "error" || item.snmp_check?.status === "error")
        .map(item => `${item.host || item.hostname || item.hostid}: ${item.message || item.snmp_check?.message || "warning"}`);
      addLog(errors > 0 ? "err" : "ok", `Manual export to Observium finished. created=${created} updated=${updated} skipped=${skipped} errors=${errors}`, detailLines.length ? detailLines.join("\n") : response);
      loadObservium();
    } catch (error) {
      addLog("err", `Manual export failed: ${error.message}`);
    }
  }

  async function archiveSelectedZabbix() {
    const ids = Object.entries(selectedZabbix).filter(([, value]) => value).map(([id]) => id);
    if (!ids.length) {
      addLog("err", "Select at least one Zabbix host to archive");
      return;
    }
    try {
      await api.bulkArchive({ source: "zabbix", ids, archive: archiveView.zabbix !== "only" });
      addLog("ok", `${ids.length} Zabbix hosts updated in archive`);
      setSelectedZabbix({});
      loadZabbix();
    } catch (error) {
      addLog("err", `Bulk archive failed: ${error.message}`);
    }
  }

  async function archiveSelectedNetbox() {
    const ids = Object.entries(selectedNetbox).filter(([, value]) => value).map(([id]) => id);
    if (!ids.length) {
      addLog("err", "Select at least one NetBox device to archive");
      return;
    }
    try {
      await api.bulkArchive({ source: "netbox", ids, archive: archiveView.netbox !== "only" });
      addLog("ok", `${ids.length} NetBox devices updated in archive`);
      setSelectedNetbox({});
      loadNetbox();
      loadCorrelations();
    } catch (error) {
      addLog("err", `Bulk archive failed: ${error.message}`);
    }
  }

  async function archiveSelectedObservium() {
    const ids = Object.entries(selectedObservium).filter(([, value]) => value).map(([id]) => id);
    if (!ids.length) {
      addLog("err", "Select at least one Observium device to archive");
      return;
    }
    try {
      await api.bulkArchive({ source: "observium", ids, archive: archiveView.observium !== "only" });
      addLog("ok", `${ids.length} Observium devices updated in archive`);
      setSelectedObservium({});
      loadObservium();
      loadCorrelations();
    } catch (error) {
      addLog("err", `Bulk archive failed: ${error.message}`);
    }
  }

  const filteredZ = useMemo(
    () => zHosts.filter(item => matches(`${item.host} ${item.name} ${(item.interfaces || []).map(i => i.ip || i.dns).join(" ")}`, filter.zabbix)),
    [filter.zabbix, zHosts]
  );
  const filteredN = useMemo(
    () => nbDevices.filter(item => matches(`${item.name} ${item.primary_ip4?.address || ""} ${item.site?.name || ""}`, filter.netbox)),
    [filter.netbox, nbDevices]
  );
  const filteredO = useMemo(
    () => obsDevices.filter(item => matches(`${item.hostname} ${item.sysName || ""} ${item.ip || ""} ${item.location || ""}`, filter.observium)),
    [filter.observium, obsDevices]
  );

  const savedCorrelationMap = useMemo(() => buildSavedCorrelationIndex(correlations), [correlations]);
  const autoCorrelationIndex = useMemo(
    () => buildAutoCorrelationIndex(zHosts, nbDevices, obsDevices, savedCorrelationMap),
    [zHosts, nbDevices, obsDevices, savedCorrelationMap]
  );
  const savedCount = useMemo(() => new Set(correlations.map(item => item.id)).size, [correlations]);
  const autoCount = useMemo(() => new Set(autoCorrelationIndex.groups.map(item => item.groupId)).size, [autoCorrelationIndex.groups]);

  const correlationMap = useMemo(() => ({
    zabbix: { ...autoCorrelationIndex.itemMap.zabbix, ...savedCorrelationMap.zabbix },
    netbox: { ...autoCorrelationIndex.itemMap.netbox, ...savedCorrelationMap.netbox },
    observium: { ...autoCorrelationIndex.itemMap.observium, ...savedCorrelationMap.observium },
  }), [autoCorrelationIndex.itemMap, savedCorrelationMap]);

  function correlationMetaFor(sourceName, id) {
    if (uiPrefs?.correlationMode === "off") return null;
    const meta = correlationMap[sourceName]?.[String(id)] || null;
    if (!meta) return null;
    if (uiPrefs?.correlationMode === "saved" && meta.kind !== "saved") return null;
    return meta;
  }

  function openWithCorrelation(sourceName, itemId, label) {
    const meta = correlationMetaFor(sourceName, itemId);
    if (!meta?.items) {
      onOpenDevice({ type: sourceName, id: itemId, label });
      return;
    }
    const devices = Object.entries(meta.items).map(([type, item]) => ({ type, id: item.id, label: item.label || label }));
    onOpenDevice({ devices, correlation: meta });
    addLog("ok", `${meta.kind === "saved" ? "Correlation group" : "Auto correlation"} opened in workspace: ${labelWithMeta(label, meta)}`);
  }

  const sections = ["zabbix", "netbox", "observium"].filter(item => source === "all" || source === item);
  const columns = sections.length === 1 ? "1fr" : sections.length === 2 ? "1fr 1fr" : "1fr 1fr 1fr";

  function archiveSelector(sourceName) {
    return (
      <select
        value={archiveView[sourceName]}
        onChange={event => {
          const value = event.target.value;
          setArchiveView(prev => ({ ...prev, [sourceName]: value }));
          if (sourceName === "zabbix") loadZabbix(value);
          if (sourceName === "netbox") loadNetbox(value);
          if (sourceName === "observium") loadObservium(value);
        }}
      >
        <option value="exclude">Active</option>
        <option value="only">Archived</option>
        <option value="all">All</option>
      </select>
    );
  }

  return (
    <div style={{ padding: 24, height: "100%", display: "flex", flexDirection: "column" }}>
      <div style={{ display: "flex", alignItems: "center", gap: 12, marginBottom: 20 }}>
        <h2 style={{ fontFamily: "var(--font-mono)", fontSize: 16, color: "var(--text)" }}>Device Inventory</h2>
        <div style={{ display: "flex", gap: 8, alignItems: "center", flexWrap: "wrap" }}>
          <span className="correlation-pill saved">saved {savedCount}</span>
          {uiPrefs?.correlationMode !== "saved" && uiPrefs?.correlationMode !== "off" && <span className="correlation-pill auto">auto {autoCount}</span>}
        </div>
        <div style={{ marginLeft: "auto", display: "flex", gap: 8 }}>
          <button className="btn-secondary" onClick={downloadInventoryDrawio} disabled={downloadingDrawio}>
            {downloadingDrawio ? "Preparing draw.io..." : "Download draw.io"}
          </button>
          <div style={{ display: "flex", background: "var(--bg3)", border: "1px solid var(--border)", borderRadius: "var(--radius)", overflow: "hidden" }}>
            {["all", "zabbix", "netbox", "observium"].map(item => (
              <button
                key={item}
                onClick={() => setSource(item)}
                style={{
                  background: source === item ? "var(--bg4)" : "transparent",
                  color: source === item ? "var(--text)" : "var(--text3)",
                  padding: "6px 12px",
                  border: "none",
                  fontSize: 11,
                  textTransform: "uppercase",
                  letterSpacing: "0.05em",
                }}
              >
                {item}
              </button>
            ))}
          </div>
          <button className="btn-primary" onClick={loadAll} disabled={loading.zabbix || loading.netbox || loading.observium}>
            {(loading.zabbix || loading.netbox || loading.observium) ? "Loading..." : "Load All"}
          </button>
        </div>
      </div>

      <div style={{ display: "grid", gridTemplateColumns: columns, gap: 20, flex: 1, overflow: "hidden" }}>
        {sections.includes("zabbix") && (
          <div style={{ display: "flex", flexDirection: "column", overflow: "hidden" }}>
            <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 10 }}>
              {panelTag("zabbix")}
              <span style={{ color: "var(--text3)", fontFamily: "var(--font-mono)", fontSize: 11 }}>{filteredZ.length} / {zHosts.length} hosts</span>
              {archiveSelector("zabbix")}
              <input placeholder="Filter..." value={filter.zabbix} onChange={event => setFilter(prev => ({ ...prev, zabbix: event.target.value }))} style={{ marginLeft: "auto", width: 140, padding: "4px 10px" }} />
              <button className="btn-secondary" style={{ padding: "4px 10px", fontSize: 10 }} onClick={() => onOpenDevice({ type: "zabbix", id: "new", label: "New Zabbix Host" })}>+ New</button>
            </div>
            <div className="flex-gap" style={{ marginBottom: 10 }}>
              <button className="btn-secondary" onClick={() => exportSelectedToObservium(Object.entries(selectedZabbix).filter(([, value]) => value).map(([id]) => id))}>Export Selected</button>
              <button className="btn-secondary" onClick={archiveSelectedZabbix}>{archiveView.zabbix === "only" ? "Restore Selected" : "Archive Selected"}</button>
            </div>
            <div className="section" style={{ flex: 1, overflow: "hidden", display: "flex", flexDirection: "column" }}>
              <div style={{ overflow: "auto", flex: 1 }}>
                <table>
                  <thead>
                    <tr><th></th><th>Host</th><th>IP</th><th>Status</th><th>Actions</th></tr>
                  </thead>
                  <tbody>
                    {loading.zabbix && <tr><td colSpan={5} style={{ textAlign: "center", color: "var(--text3)", padding: 20 }}>Loading...</td></tr>}
                    {!loading.zabbix && filteredZ.map(host => {
                      const mainIp = zabbixMainIp(host) || "-";
                      const meta = correlationMetaFor("zabbix", host.hostid);
                      return (
                        <tr key={host.hostid} className={meta ? `row-correlation-${meta.kind}` : ""}>
                          <td><input type="checkbox" checked={!!selectedZabbix[host.hostid]} onChange={event => setSelectedZabbix(prev => ({ ...prev, [host.hostid]: event.target.checked }))} /></td>
                          <td>
                            <div style={{ fontWeight: 600, display: "flex", alignItems: "center", gap: 8, flexWrap: "wrap" }}>
                              {host.ui_url ? (
                                <a href={host.ui_url} target="_blank" rel="noreferrer" style={linkStyle()} title="Open in Zabbix">
                                  {host.host}
                                </a>
                              ) : (
                                <span>{host.host}</span>
                              )}
                              {meta && linkedPlatformBadges("zabbix", meta)}
                            </div>
                            <div style={{ color: "var(--text3)", fontSize: 10 }}>{host.name !== host.host ? host.name : ""}</div>
                          </td>
                          <td style={{ color: "var(--accent2)" }}>{mainIp}</td>
                          <td><span className={`tag ${host.archived ? "tag-warn" : host.status === "0" ? "tag-ok" : "tag-err"}`} style={{ fontSize: 9 }}>{host.archived ? "archived" : host.status === "0" ? "on" : "off"}</span></td>
                          <td>
                            <div className="flex-gap">
                              <button className="btn-secondary" style={{ padding: "2px 8px", fontSize: 10 }} onClick={() => openWithCorrelation("zabbix", host.hostid, host.host)}>{meta ? "Open Group" : "Open"}</button>
                              <button className="btn-secondary" style={{ padding: "2px 8px", fontSize: 10 }} onClick={() => exportSelectedToObservium([host.hostid])}>Export OBS</button>
                              <button className="btn-secondary" style={{ padding: "2px 8px", fontSize: 10 }} onClick={() => toggleArchive("zabbix", { id: host.hostid, label: host.host, details: { host: host.host } }, host.archived)}>{host.archived ? "Restore" : "Archive"}</button>
                            </div>
                          </td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              </div>
            </div>
          </div>
        )}

        {sections.includes("netbox") && (
          <div style={{ display: "flex", flexDirection: "column", overflow: "hidden" }}>
            <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 10 }}>
              {panelTag("netbox")}
              <span style={{ color: "var(--text3)", fontFamily: "var(--font-mono)", fontSize: 11 }}>{filteredN.length} / {nbDevices.length} devices</span>
              {archiveSelector("netbox")}
              <input placeholder="Filter..." value={filter.netbox} onChange={event => setFilter(prev => ({ ...prev, netbox: event.target.value }))} style={{ marginLeft: "auto", width: 140, padding: "4px 10px" }} />
              <button className="btn-secondary" style={{ padding: "4px 10px", fontSize: 10 }} onClick={() => onOpenDevice({ type: "netbox", id: "new", label: "New NetBox Device" })}>+ New</button>
            </div>
            <div className="flex-gap" style={{ marginBottom: 10 }}>
              <button className="btn-secondary" onClick={archiveSelectedNetbox}>{archiveView.netbox === "only" ? "Restore Selected" : "Archive Selected"}</button>
            </div>
            <div className="section" style={{ flex: 1, overflow: "hidden", display: "flex", flexDirection: "column" }}>
              <div style={{ overflow: "auto", flex: 1 }}>
                <table>
                  <thead>
                    <tr><th></th><th>Name</th><th>IP</th><th>Status</th><th>Actions</th></tr>
                  </thead>
                  <tbody>
                    {loading.netbox && <tr><td colSpan={5} style={{ textAlign: "center", color: "var(--text3)", padding: 20 }}>Loading...</td></tr>}
                    {!loading.netbox && filteredN.map(device => {
                      const meta = correlationMetaFor("netbox", device.id);
                      return (
                      <tr key={device.id} className={meta ? `row-correlation-${meta.kind}` : ""}>
                        <td><input type="checkbox" checked={!!selectedNetbox[device.id]} onChange={event => setSelectedNetbox(prev => ({ ...prev, [device.id]: event.target.checked }))} /></td>
                        <td><div style={{ fontWeight: 600, display: "flex", alignItems: "center", gap: 8, flexWrap: "wrap" }}>{device.display_url ? <a href={device.display_url} target="_blank" rel="noreferrer" style={linkStyle()} title="Open in NetBox">{device.name}</a> : <span>{device.name}</span>}{meta && linkedPlatformBadges("netbox", meta)}</div><div style={{ color: "var(--text3)", fontSize: 10 }}>{device.device_type?.display || ""}</div></td>
                        <td style={{ color: "var(--accent2)" }}>{device.primary_ip4?.address || "-"}</td>
                        <td><span className={`tag ${device.archived ? "tag-warn" : device.status?.value === "active" ? "tag-ok" : "tag-warn"}`} style={{ fontSize: 9 }}>{device.archived ? "archived" : (device.status?.value || device.status)}</span></td>
                        <td><div className="flex-gap"><button className="btn-secondary" style={{ padding: "2px 8px", fontSize: 10 }} onClick={() => openWithCorrelation("netbox", device.id, device.name)}>{meta ? "Open Group" : "Open"}</button><button className="btn-secondary" style={{ padding: "2px 8px", fontSize: 10 }} onClick={() => toggleArchive("netbox", { id: device.id, label: device.name }, device.archived)}>{device.archived ? "Restore" : "Archive"}</button></div></td>
                      </tr>
                    )})}
                  </tbody>
                </table>
              </div>
            </div>
          </div>
        )}

        {sections.includes("observium") && (
          <div style={{ display: "flex", flexDirection: "column", overflow: "hidden" }}>
            <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 10 }}>
              {panelTag("observium")}
              <span style={{ color: "var(--text3)", fontFamily: "var(--font-mono)", fontSize: 11 }}>{filteredO.length} / {obsDevices.length} devices</span>
              {archiveSelector("observium")}
              <input placeholder="Filter..." value={filter.observium} onChange={event => setFilter(prev => ({ ...prev, observium: event.target.value }))} style={{ marginLeft: "auto", width: 120, padding: "4px 10px" }} />
              <button className="btn-secondary" style={{ padding: "4px 10px", fontSize: 10 }} onClick={() => onOpenDevice({ type: "observium", id: "new", label: "New Observium Device" })}>+ New</button>
            </div>
            <div className="flex-gap" style={{ marginBottom: 10 }}>
              <button className="btn-secondary" onClick={archiveSelectedObservium}>{archiveView.observium === "only" ? "Restore Selected" : "Archive Selected"}</button>
            </div>
            <div className="section" style={{ flex: 1, overflow: "hidden", display: "flex", flexDirection: "column" }}>
              <div style={{ overflow: "auto", flex: 1 }}>
                <table>
                  <thead>
                    <tr><th></th><th>Hostname</th><th>SNMP</th><th>Status</th><th>Actions</th></tr>
                  </thead>
                  <tbody>
                    {loading.observium && <tr><td colSpan={5} style={{ textAlign: "center", color: "var(--text3)", padding: 20 }}>Loading...</td></tr>}
                    {!loading.observium && filteredO.map(device => {
                      const meta = correlationMetaFor("observium", device.device_id);
                      return (
                      <tr key={device.device_id} className={meta ? `row-correlation-${meta.kind}` : ""}>
                        <td><input type="checkbox" checked={!!selectedObservium[device.device_id]} onChange={event => setSelectedObservium(prev => ({ ...prev, [device.device_id]: event.target.checked }))} /></td>
                        <td><div style={{ fontWeight: 600, display: "flex", alignItems: "center", gap: 8, flexWrap: "wrap" }}>{device.web_url ? <a href={device.web_url} target="_blank" rel="noreferrer" style={linkStyle()} title="Open in Observium">{device.hostname}</a> : <span>{device.hostname}</span>}{meta && linkedPlatformBadges("observium", meta)}</div><div style={{ color: "var(--text3)", fontSize: 10 }}>{device.sysName || device.ip || ""}</div></td>
                        <td style={{ color: "var(--accent2)" }}>{device.snmp_version}/{device.snmp_port}</td>
                        <td><span className={`tag ${device.archived ? "tag-warn" : device.disabled ? "tag-warn" : "tag-ok"}`} style={{ fontSize: 9 }}>{device.archived ? "archived" : device.disabled ? "disabled" : (device.status ? "up" : "down")}</span></td>
                        <td><div className="flex-gap"><button className="btn-secondary" style={{ padding: "2px 8px", fontSize: 10 }} onClick={() => openWithCorrelation("observium", device.device_id, device.hostname)}>{meta ? "Open Group" : "Open"}</button><button className="btn-secondary" style={{ padding: "2px 8px", fontSize: 10 }} onClick={() => toggleArchive("observium", { id: device.device_id, label: device.hostname }, device.archived)}>{device.archived ? "Restore" : "Archive"}</button></div></td>
                      </tr>
                    )})}
                  </tbody>
                </table>
              </div>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
