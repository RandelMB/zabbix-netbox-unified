import { useMemo, useState } from "react";
import { api } from "../utils/api";
import { useLogs } from "../hooks/useLogs";

function matches(item, text) {
  return text.trim() === "" || item.toLowerCase().includes(text.trim().toLowerCase());
}

function panelTag(type) {
  if (type === "zabbix") return <span className="tag tag-zabbix">ZABBIX</span>;
  if (type === "netbox") return <span className="tag tag-netbox">NETBOX</span>;
  return <span className="tag" style={{ background: "rgba(255,172,48,0.18)", color: "#ffac30", borderColor: "rgba(255,172,48,0.45)" }}>OBSERVIUM</span>;
}

export function DeviceListPage({ onOpenDevice }) {
  const { addLog } = useLogs();
  const [zHosts, setZHosts] = useState([]);
  const [nbDevices, setNbDevices] = useState([]);
  const [obsDevices, setObsDevices] = useState([]);
  const [loading, setLoading] = useState({ zabbix: false, netbox: false, observium: false });
  const [filter, setFilter] = useState({ zabbix: "", netbox: "", observium: "" });
  const [archiveView, setArchiveView] = useState({ zabbix: "exclude", netbox: "exclude", observium: "exclude" });
  const [selectedZabbix, setSelectedZabbix] = useState({});
  const [source, setSource] = useState("all");

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
      const response = await api.exportZabbixToObservium({ hostids, run_discovery: true, run_poller: true, update_existing: true });
      const created = response.result.filter(item => item.status === "created").length;
      const updated = response.result.filter(item => item.status === "updated").length;
      const skipped = response.result.filter(item => item.status === "skipped").length;
      const errors = response.result.filter(item => item.status === "error").length;
      addLog("ok", `Manual export to Observium finished. created=${created} updated=${updated} skipped=${skipped} errors=${errors}`, response);
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
        <div style={{ marginLeft: "auto", display: "flex", gap: 8 }}>
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
                      const mainIp = (host.interfaces || []).find(item => item.main === "1")?.ip || host.interfaces?.[0]?.ip || "-";
                      return (
                        <tr key={host.hostid}>
                          <td><input type="checkbox" checked={!!selectedZabbix[host.hostid]} onChange={event => setSelectedZabbix(prev => ({ ...prev, [host.hostid]: event.target.checked }))} /></td>
                          <td>
                            <div style={{ fontWeight: 600 }}>{host.host}</div>
                            <div style={{ color: "var(--text3)", fontSize: 10 }}>{host.name !== host.host ? host.name : ""}</div>
                          </td>
                          <td style={{ color: "var(--accent2)" }}>{mainIp}</td>
                          <td><span className={`tag ${host.archived ? "tag-warn" : host.status === "0" ? "tag-ok" : "tag-err"}`} style={{ fontSize: 9 }}>{host.archived ? "archived" : host.status === "0" ? "on" : "off"}</span></td>
                          <td>
                            <div className="flex-gap">
                              <button className="btn-secondary" style={{ padding: "2px 8px", fontSize: 10 }} onClick={() => onOpenDevice({ type: "zabbix", id: host.hostid, label: host.host })}>Open</button>
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
            </div>
            <div className="section" style={{ flex: 1, overflow: "hidden", display: "flex", flexDirection: "column" }}>
              <div style={{ overflow: "auto", flex: 1 }}>
                <table>
                  <thead>
                    <tr><th>Name</th><th>IP</th><th>Status</th><th>Actions</th></tr>
                  </thead>
                  <tbody>
                    {loading.netbox && <tr><td colSpan={4} style={{ textAlign: "center", color: "var(--text3)", padding: 20 }}>Loading...</td></tr>}
                    {!loading.netbox && filteredN.map(device => (
                      <tr key={device.id}>
                        <td><div style={{ fontWeight: 600 }}>{device.name}</div><div style={{ color: "var(--text3)", fontSize: 10 }}>{device.device_type?.display || ""}</div></td>
                        <td style={{ color: "var(--accent2)" }}>{device.primary_ip4?.address || "-"}</td>
                        <td><span className={`tag ${device.archived ? "tag-warn" : device.status?.value === "active" ? "tag-ok" : "tag-warn"}`} style={{ fontSize: 9 }}>{device.archived ? "archived" : (device.status?.value || device.status)}</span></td>
                        <td><div className="flex-gap"><button className="btn-secondary" style={{ padding: "2px 8px", fontSize: 10 }} onClick={() => onOpenDevice({ type: "netbox", id: device.id, label: device.name })}>Open</button><button className="btn-secondary" style={{ padding: "2px 8px", fontSize: 10 }} onClick={() => toggleArchive("netbox", { id: device.id, label: device.name }, device.archived)}>{device.archived ? "Restore" : "Archive"}</button></div></td>
                      </tr>
                    ))}
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
            <div className="section" style={{ flex: 1, overflow: "hidden", display: "flex", flexDirection: "column" }}>
              <div style={{ overflow: "auto", flex: 1 }}>
                <table>
                  <thead>
                    <tr><th>Hostname</th><th>SNMP</th><th>Status</th><th>Actions</th></tr>
                  </thead>
                  <tbody>
                    {loading.observium && <tr><td colSpan={4} style={{ textAlign: "center", color: "var(--text3)", padding: 20 }}>Loading...</td></tr>}
                    {!loading.observium && filteredO.map(device => (
                      <tr key={device.device_id}>
                        <td><div style={{ fontWeight: 600 }}>{device.hostname}</div><div style={{ color: "var(--text3)", fontSize: 10 }}>{device.sysName || device.ip || ""}</div></td>
                        <td style={{ color: "var(--accent2)" }}>{device.snmp_version}/{device.snmp_port}</td>
                        <td><span className={`tag ${device.archived ? "tag-warn" : device.disabled ? "tag-warn" : "tag-ok"}`} style={{ fontSize: 9 }}>{device.archived ? "archived" : device.disabled ? "disabled" : (device.status ? "up" : "down")}</span></td>
                        <td><div className="flex-gap"><button className="btn-secondary" style={{ padding: "2px 8px", fontSize: 10 }} onClick={() => onOpenDevice({ type: "observium", id: device.device_id, label: device.hostname })}>Open</button><button className="btn-secondary" style={{ padding: "2px 8px", fontSize: 10 }} onClick={() => toggleArchive("observium", { id: device.device_id, label: device.hostname }, device.archived)}>{device.archived ? "Restore" : "Archive"}</button></div></td>
                      </tr>
                    ))}
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
