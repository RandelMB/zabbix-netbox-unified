import { useEffect, useMemo, useState } from "react";
import { api } from "../utils/api";
import { useLogs } from "../hooks/useLogs";

function errorMessage(error) {
  if (!error) return "Unknown error";
  if (typeof error === "string") return error;
  if (error instanceof Error) return error.message;
  if (typeof error === "object") return JSON.stringify(error);
  return String(error);
}

function formatValue(value) {
  if (value === null || value === undefined || value === "") return "-";
  if (Array.isArray(value)) return value.length ? value.join(", ") : "-";
  return String(value);
}

export function DiscoveryPage({ active }) {
  const { addLog } = useLogs();
  const [sites, setSites] = useState([]);
  const [roles, setRoles] = useState([]);
  const [deviceTypes, setDeviceTypes] = useState([]);
  const [zabbixHosts, setZabbixHosts] = useState([]);
  const [observiumDevices, setObserviumDevices] = useState([]);
  const [loadingSources, setLoadingSources] = useState(false);

  const [lldpSource, setLldpSource] = useState("observium");
  const [lldpSourceId, setLldpSourceId] = useState("");
  const [lldpSearch, setLldpSearch] = useState("");
  const [lldpPreview, setLldpPreview] = useState(null);
  const [lldpLoading, setLldpLoading] = useState(false);
  const [lldpSelection, setLldpSelection] = useState({});
  const [lldpSiteId, setLldpSiteId] = useState("");
  const [lldpRoleId, setLldpRoleId] = useState("");

  const [snmpProbe, setSnmpProbe] = useState(null);
  const [snmpLoading, setSnmpLoading] = useState(false);
  const [snmpForm, setSnmpForm] = useState({
    ip: "",
    snmp_version: "v2c",
    snmp_community: "",
    snmp_port: 161,
    site_id: "",
    role_id: "",
    device_type_id: "",
    name: "",
    serial: "",
    description: "",
  });

  useEffect(() => {
    if (!active) return;
    loadBase();
  }, [active]);

  async function loadBase() {
    setLoadingSources(true);
    try {
      const [sitesR, rolesR, typesR, zbR, obsR] = await Promise.all([
        api.netboxSites(),
        api.netboxRoles(),
        api.netboxDeviceTypes(),
        api.zabbixHosts(1000, "exclude"),
        api.observiumDevices(1000, "", "exclude"),
      ]);
      const nextSites = sitesR.result?.results || [];
      const nextRoles = rolesR.result?.results || [];
      setSites(nextSites);
      setRoles(nextRoles);
      setDeviceTypes(typesR.result?.results || []);
      setZabbixHosts(zbR.result || []);
      setObserviumDevices(obsR.result || []);
      if (!lldpSiteId && nextSites[0]?.id) setLldpSiteId(String(nextSites[0].id));
      if (!lldpRoleId && nextRoles[0]?.id) setLldpRoleId(String(nextRoles[0].id));
      setSnmpForm(prev => ({
        ...prev,
        site_id: prev.site_id || String(nextSites[0]?.id || ""),
        role_id: prev.role_id || String(nextRoles[0]?.id || ""),
      }));
      addLog("ok", `Discovery sources loaded: zabbix=${(zbR.result || []).length} observium=${(obsR.result || []).length}`);
    } catch (error) {
      addLog("err", `Discovery source load failed: ${errorMessage(error)}`);
    }
    setLoadingSources(false);
  }

  const lldpOptions = useMemo(() => {
    const sourceItems = lldpSource === "zabbix" ? zabbixHosts : observiumDevices;
    const needle = lldpSearch.trim().toLowerCase();
    return sourceItems.filter(item => {
      const text = lldpSource === "zabbix"
        ? `${item.host || ""} ${item.name || ""} ${item.interfaces?.[0]?.ip || ""}`.toLowerCase()
        : `${item.hostname || ""} ${item.sysName || ""} ${item.ip || ""}`.toLowerCase();
      return !needle || text.includes(needle);
    });
  }, [lldpSearch, lldpSource, observiumDevices, zabbixHosts]);

  function lldpOptionLabel(item) {
    if (lldpSource === "zabbix") {
      const ip = (item.interfaces || []).find(entry => entry.main === "1")?.ip || item.interfaces?.[0]?.ip || "";
      return `${item.host || item.name || item.hostid} ${ip ? `· ${ip}` : ""}`;
    }
    return `${item.hostname || item.sysName || item.device_id} ${item.ip ? `· ${item.ip}` : ""}`;
  }

  function toggleLldpSelection(id, checked) {
    setLldpSelection(prev => ({ ...prev, [id]: checked }));
  }

  async function previewLldp() {
    if (!lldpSourceId) {
      addLog("err", "Select a source device first");
      return;
    }
    setLldpLoading(true);
    try {
      const response = await api.post("/api/discovery/lldp/preview", { source: lldpSource, source_id: lldpSourceId });
      const preview = response.result || null;
      setLldpPreview(preview);
      const nextSelection = {};
      (preview?.proposals || []).forEach(item => {
        if (item.status === "proposed_create") nextSelection[item.id] = true;
      });
      setLldpSelection(nextSelection);
      addLog("ok", `LLDP preview loaded: links=${preview?.summary?.links || 0}`, response);
    } catch (error) {
      setLldpPreview(null);
      addLog("err", `LLDP preview failed: ${errorMessage(error)}`);
    }
    setLldpLoading(false);
  }

  async function applyLldp() {
    setLldpLoading(true);
    try {
      const proposal_ids = Object.entries(lldpSelection).filter(([, enabled]) => enabled).map(([id]) => id);
      const response = await api.post("/api/discovery/lldp/apply", {
        source: lldpSource,
        source_id: lldpSourceId,
        proposal_ids,
        site_id: Number(lldpSiteId || 0),
        role_id: Number(lldpRoleId || 0),
      });
      const results = response.result?.results || [];
      const created = results.filter(item => item.status === "created").length;
      const errors = results.filter(item => item.status === "error").length;
      setLldpPreview(response.result?.preview || null);
      addLog(errors ? "err" : "ok", `LLDP create finished: created=${created} errors=${errors}`, response);
    } catch (error) {
      addLog("err", `LLDP create failed: ${errorMessage(error)}`);
    }
    setLldpLoading(false);
  }

  function setSnmpField(field, value) {
    setSnmpForm(prev => ({ ...prev, [field]: value }));
  }

  async function previewSnmp() {
    setSnmpLoading(true);
    try {
      const response = await api.post("/api/netbox/snmp-discovery/preview", {
        ip: snmpForm.ip,
        snmp_version: snmpForm.snmp_version,
        snmp_community: snmpForm.snmp_community,
        snmp_port: Number(snmpForm.snmp_port || 161),
        site_id: Number(snmpForm.site_id || 0),
        role_id: Number(snmpForm.role_id || 0),
      });
      const result = response.result || null;
      setSnmpProbe(result);
      setSnmpForm(prev => ({
        ...prev,
        device_type_id: prev.device_type_id || String(result?.proposed_netbox_fields?.device_type || ""),
        name: result?.proposed_netbox_fields?.name || "",
        serial: result?.proposed_netbox_fields?.serial || "",
        description: result?.proposed_netbox_fields?.description || "",
      }));
      addLog("ok", `SNMP preview loaded for ${snmpForm.ip}`, response);
    } catch (error) {
      setSnmpProbe(null);
      addLog("err", `SNMP preview failed: ${errorMessage(error)}`);
    }
    setSnmpLoading(false);
  }

  async function importSnmp() {
    setSnmpLoading(true);
    try {
      const response = await api.post("/api/netbox/snmp-discovery/import", {
        ip: snmpForm.ip,
        snmp_version: snmpForm.snmp_version,
        snmp_community: snmpForm.snmp_community,
        snmp_port: Number(snmpForm.snmp_port || 161),
        site_id: Number(snmpForm.site_id || 0),
        role_id: Number(snmpForm.role_id || 0),
        device_type_id: snmpForm.device_type_id ? Number(snmpForm.device_type_id) : null,
        name: snmpForm.name,
        serial: snmpForm.serial,
        description: snmpForm.description,
      });
      addLog("ok", `SNMP import created device: ${response.result?.device?.name || snmpForm.name}`, response);
      setSnmpProbe(response.result?.probe || snmpProbe);
    } catch (error) {
      addLog("err", `SNMP import failed: ${errorMessage(error)}`);
    }
    setSnmpLoading(false);
  }

  return (
    <div style={{ padding: 18, display: "grid", gap: 16 }}>
      <div className="section">
        <div className="section-header"><span>LLDP Neighbor Discovery</span></div>
        <div className="section-body">
          <div className="grid-4" style={{ marginBottom: 12 }}>
            <div className="field-row">
              <label>Source Platform</label>
              <select value={lldpSource} onChange={e => { setLldpSource(e.target.value); setLldpSourceId(""); setLldpPreview(null); }}>
                <option value="observium">Observium</option>
                <option value="zabbix">Zabbix</option>
              </select>
            </div>
            <div className="field-row">
              <label>Search Device1</label>
              <input value={lldpSearch} onChange={e => setLldpSearch(e.target.value)} placeholder="hostname, sysName or IP" />
            </div>
            <div className="field-row" style={{ gridColumn: "span 2" }}>
              <label>Device1</label>
              <select value={lldpSourceId} onChange={e => setLldpSourceId(e.target.value)}>
                <option value="">Select source device</option>
                {lldpOptions.map(item => (
                  <option key={lldpSource === "zabbix" ? item.hostid : item.device_id} value={lldpSource === "zabbix" ? item.hostid : item.device_id}>
                    {lldpOptionLabel(item)}
                  </option>
                ))}
              </select>
            </div>
          </div>
          <div className="flex-gap" style={{ marginBottom: 12 }}>
            <button className="btn-secondary" onClick={loadBase} disabled={loadingSources}>{loadingSources ? "Refreshing..." : "Refresh Sources"}</button>
            <button className="btn-primary" onClick={previewLldp} disabled={lldpLoading}>{lldpLoading ? "Loading LLDP..." : "Preview LLDP Neighbors"}</button>
          </div>

          {lldpPreview && (
            <>
              <div className="grid-4" style={{ marginBottom: 12 }}>
                <div className="section" style={{ padding: 12 }}><div style={{ color: "var(--text3)", fontSize: 11 }}>Links</div><div style={{ fontFamily: "var(--font-mono)", fontSize: 18 }}>{lldpPreview.summary?.links || 0}</div></div>
                <div className="section" style={{ padding: 12 }}><div style={{ color: "var(--text3)", fontSize: 11 }}>Matched</div><div style={{ fontFamily: "var(--font-mono)", fontSize: 18 }}>{lldpPreview.summary?.matched || 0}</div></div>
                <div className="section" style={{ padding: 12 }}><div style={{ color: "var(--text3)", fontSize: 11 }}>Create</div><div style={{ fontFamily: "var(--font-mono)", fontSize: 18 }}>{lldpPreview.summary?.proposed_create || 0}</div></div>
                <div className="section" style={{ padding: 12 }}>
                  <div style={{ color: "var(--text3)", fontSize: 11 }}>Resolved Source</div>
                  <div style={{ fontSize: 12 }}>{lldpPreview.source?.observium?.sysName || lldpPreview.source?.observium?.hostname || "-"}</div>
                </div>
              </div>

              <div className="grid-2" style={{ marginBottom: 12 }}>
                <div className="field-row">
                  <label>Site for Create</label>
                  <select value={lldpSiteId} onChange={e => setLldpSiteId(e.target.value)}>
                    <option value="">Select site</option>
                    {sites.map(item => <option key={item.id} value={item.id}>{item.name}</option>)}
                  </select>
                </div>
                <div className="field-row">
                  <label>Role for Create</label>
                  <select value={lldpRoleId} onChange={e => setLldpRoleId(e.target.value)}>
                    <option value="">Select role</option>
                    {roles.map(item => <option key={item.id} value={item.id}>{item.name}</option>)}
                  </select>
                </div>
              </div>

              <div style={{ display: "grid", gap: 10, marginBottom: 12 }}>
                {(lldpPreview.proposals || []).map(item => (
                  <div key={item.id} className="section" style={{ padding: 12 }}>
                    <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 8, flexWrap: "wrap" }}>
                      {item.status === "proposed_create" && <input type="checkbox" checked={!!lldpSelection[item.id]} onChange={e => toggleLldpSelection(item.id, e.target.checked)} />}
                      <strong>{item.neighbor?.name || item.neighbor?.ip || item.neighbor?.mac_address || item.id}</strong>
                      <span className={`tag ${item.status === "matched" ? "tag-ok" : "tag-netbox"}`}>{item.status.replaceAll("_", " ")}</span>
                      <span className="tag tag-observium">{item.protocol}</span>
                    </div>
                    <div className="grid-2">
                      <div style={{ color: "var(--text2)", fontSize: 12 }}>
                        <div>Local port: {formatValue(item.local_port?.name)}</div>
                        <div>Neighbor IP: {formatValue(item.neighbor?.ip)}</div>
                        <div>Neighbor MAC: {formatValue(item.neighbor?.mac_address)}</div>
                        <div>Remote port: {formatValue(item.remote_port?.name)}</div>
                      </div>
                      <div style={{ color: "var(--text2)", fontSize: 12 }}>
                        <div>NetBox match: {formatValue(item.matches?.netbox?.name)}</div>
                        <div>Zabbix match: {formatValue(item.matches?.zabbix?.host || item.matches?.zabbix?.name)}</div>
                        <div>Observium match: {formatValue(item.matches?.observium?.hostname || item.matches?.observium?.sysName)}</div>
                        <div>Model candidate: {formatValue(item.create_candidate?.model_name)}</div>
                      </div>
                    </div>
                  </div>
                ))}
              </div>
              <div className="flex-gap">
                <button className="btn-primary" onClick={applyLldp} disabled={lldpLoading}>Create Selected in NetBox</button>
              </div>
            </>
          )}
        </div>
      </div>

      <div className="section">
        <div className="section-header"><span>Add Device to NetBox by SNMP</span></div>
        <div className="section-body">
          <div className="grid-4" style={{ marginBottom: 12 }}>
            <div className="field-row"><label>IP</label><input value={snmpForm.ip} onChange={e => setSnmpField("ip", e.target.value)} placeholder="172.25.200.3" /></div>
            <div className="field-row"><label>Community</label><input value={snmpForm.snmp_community} onChange={e => setSnmpField("snmp_community", e.target.value)} placeholder="public / RO" /></div>
            <div className="field-row"><label>Site</label><select value={snmpForm.site_id} onChange={e => setSnmpField("site_id", e.target.value)}>{sites.map(item => <option key={item.id} value={item.id}>{item.name}</option>)}</select></div>
            <div className="field-row"><label>Role</label><select value={snmpForm.role_id} onChange={e => setSnmpField("role_id", e.target.value)}>{roles.map(item => <option key={item.id} value={item.id}>{item.name}</option>)}</select></div>
          </div>
          <div className="flex-gap" style={{ marginBottom: 12 }}>
            <button className="btn-secondary" onClick={previewSnmp} disabled={snmpLoading}>{snmpLoading ? "Running SNMP..." : "Preview via SNMP"}</button>
          </div>
          {snmpProbe && (
            <>
              <div className="grid-2" style={{ marginBottom: 12 }}>
                <div className="section" style={{ padding: 12 }}>
                  <div style={{ fontFamily: "var(--font-mono)", fontSize: 12, marginBottom: 8 }}>Extracted</div>
                  <div style={{ color: "var(--text2)", fontSize: 12 }}>sysName: {formatValue(snmpProbe.extracted?.sysName)}</div>
                  <div style={{ color: "var(--text2)", fontSize: 12 }}>Vendor: {formatValue(snmpProbe.extracted?.vendor)}</div>
                  <div style={{ color: "var(--text2)", fontSize: 12 }}>Model: {formatValue(snmpProbe.extracted?.model)}</div>
                  <div style={{ color: "var(--text2)", fontSize: 12 }}>Serial: {formatValue(snmpProbe.extracted?.serial)}</div>
                  <div style={{ color: "var(--text2)", fontSize: 12 }}>Description: {formatValue(snmpProbe.proposed_netbox_fields?.description)}</div>
                  {snmpProbe.official_reference_url && <div style={{ marginTop: 8 }}><a href={snmpProbe.official_reference_url} target="_blank" rel="noreferrer">Official validation link</a></div>}
                </div>
                <div className="section" style={{ padding: 12 }}>
                  <div style={{ fontFamily: "var(--font-mono)", fontSize: 12, marginBottom: 8 }}>Matches</div>
                  <div style={{ color: "var(--text2)", fontSize: 12 }}>NetBox: {formatValue(snmpProbe.matches?.netbox?.name)}</div>
                  <div style={{ color: "var(--text2)", fontSize: 12 }}>Zabbix: {formatValue(snmpProbe.matches?.zabbix?.host || snmpProbe.matches?.zabbix?.name)}</div>
                  <div style={{ color: "var(--text2)", fontSize: 12 }}>Observium: {formatValue(snmpProbe.matches?.observium?.hostname || snmpProbe.matches?.observium?.sysName)}</div>
                  <div style={{ color: "var(--text2)", fontSize: 12 }}>Device type match: {formatValue(snmpProbe.matches?.device_type?.display || snmpProbe.matches?.device_type?.model)}</div>
                  <div style={{ color: "var(--text2)", fontSize: 12 }}>Auto create type if missing: Yes</div>
                </div>
              </div>

              <div className="grid-3" style={{ marginBottom: 12 }}>
                <div className="field-row"><label>Name</label><input value={snmpForm.name} onChange={e => setSnmpField("name", e.target.value)} /></div>
                <div className="field-row"><label>Serial</label><input value={snmpForm.serial} onChange={e => setSnmpField("serial", e.target.value)} /></div>
                <div className="field-row">
                  <label>Device Type Override</label>
                  <select value={snmpForm.device_type_id} onChange={e => setSnmpField("device_type_id", e.target.value)}>
                    <option value="">Auto-create if needed</option>
                    {deviceTypes.map(item => <option key={item.id} value={item.id}>{item.display || item.model}</option>)}
                  </select>
                </div>
              </div>
              <div className="field-row" style={{ marginBottom: 12 }}>
                <label>Description</label>
                <textarea value={snmpForm.description} onChange={e => setSnmpField("description", e.target.value)} style={{ height: 84, resize: "vertical" }} />
              </div>
              <div className="flex-gap">
                <button className="btn-primary" onClick={importSnmp} disabled={snmpLoading}>Create in NetBox</button>
              </div>
            </>
          )}
        </div>
      </div>
    </div>
  );
}
