import { useEffect, useState } from "react";
import { api } from "../utils/api";
import { useLogs } from "../hooks/useLogs";

export function SettingsPage({ uiPrefs, onUiPrefsChange, themePresets }) {
  const { addLog } = useLogs();
  const [creds, setCreds] = useState({
    zabbix_url: "",
    zabbix_token: "",
    zabbix_user: "",
    zabbix_pass: "",
    netbox_url: "",
    netbox_token: "",
  });
  const [status, setStatus] = useState({ zabbix: null, netbox: null, observium: null });
  const [checking, setChecking] = useState(false);
  const [mapping, setMapping] = useState("");
  const [mappingError, setMappingError] = useState(null);
  const [exportLogs, setExportLogs] = useState([]);

  async function reloadExportLogs() {
    try {
      const response = await api.exportLogs(100);
      setExportLogs(response.result || []);
    } catch {
      setExportLogs([]);
    }
  }

  useEffect(() => {
    api.getCredentials().then(data => {
      setCreds(prev => ({
        ...prev,
        ...data,
        zabbix_token: data.zabbix_token === "***" ? prev.zabbix_token : data.zabbix_token,
        netbox_token: data.netbox_token === "***" ? prev.netbox_token : data.netbox_token,
      }));
    }).catch(() => {});
    api.getMapping().then(data => setMapping(JSON.stringify(data, null, 2))).catch(() => {});
    reloadExportLogs();
  }, []);

  async function saveCreds() {
    try {
      const payload = Object.fromEntries(Object.entries(creds).filter(([, value]) => value));
      await api.setCredentials(payload);
      addLog("ok", "Credentials saved");
    } catch (error) {
      addLog("err", "Failed to save credentials: " + error.message);
    }
  }

  async function checkAll() {
    setChecking(true);
    try {
      await api.setCredentials(Object.fromEntries(Object.entries(creds).filter(([, value]) => value)));
    } catch {}

    const [zabbix, netbox, observium] = await Promise.all([
      api.checkZabbix().catch(error => ({ ok: false, error: error.message })),
      api.checkNetbox().catch(error => ({ ok: false, error: error.message })),
      api.checkObservium().catch(error => ({ ok: false, error: error.message })),
    ]);

    setStatus({ zabbix, netbox, observium });
    addLog(zabbix.ok ? "ok" : "err", `Zabbix: ${zabbix.ok ? "Connected v" + zabbix.version : zabbix.error}`);
    addLog(netbox.ok ? "ok" : "err", `NetBox: ${netbox.ok ? "Connected v" + netbox.version : netbox.error}`);
    addLog(observium.ok ? "ok" : "err", `Observium: ${observium.ok ? "DB ok (" + observium.devices + " devices)" : observium.error}`);
    setChecking(false);
  }

  async function saveMapping() {
    try {
      const parsed = JSON.parse(mapping);
      await api.setMapping(parsed);
      addLog("ok", "Field mapping saved");
      setMappingError(null);
    } catch (error) {
      setMappingError(error.message);
    }
  }

  function statusTag(value) {
    if (!value) return null;
    return (
      <span>
        <span className={`status-dot ${value.ok ? "dot-ok" : "dot-err"}`} />
        {value.ok ? "OK" : "Error"}
      </span>
    );
  }

  return (
    <div style={{ padding: 24, maxWidth: 1200 }}>
      <h2 style={{ marginBottom: 24, fontFamily: "var(--font-mono)", fontSize: 16, color: "var(--accent)" }}>Configuration</h2>

      <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr 1fr", gap: 20, marginBottom: 24 }}>
        <div className="section">
          <div className="section-header"><span>Zabbix</span>{statusTag(status.zabbix)}</div>
          <div className="section-body">
            <div className="field-row"><label>URL</label><input value={creds.zabbix_url} onChange={event => setCreds(prev => ({ ...prev, zabbix_url: event.target.value }))} /></div>
            <div className="field-row"><label>API Token</label><input type="password" value={creds.zabbix_token} onChange={event => setCreds(prev => ({ ...prev, zabbix_token: event.target.value }))} /></div>
            <div className="field-row"><label>Username</label><input value={creds.zabbix_user} onChange={event => setCreds(prev => ({ ...prev, zabbix_user: event.target.value }))} /></div>
            <div className="field-row"><label>Password</label><input type="password" value={creds.zabbix_pass} onChange={event => setCreds(prev => ({ ...prev, zabbix_pass: event.target.value }))} /></div>
          </div>
        </div>

        <div className="section">
          <div className="section-header"><span>NetBox</span>{statusTag(status.netbox)}</div>
          <div className="section-body">
            <div className="field-row"><label>URL</label><input value={creds.netbox_url} onChange={event => setCreds(prev => ({ ...prev, netbox_url: event.target.value }))} /></div>
            <div className="field-row"><label>API Token</label><input type="password" value={creds.netbox_token} onChange={event => setCreds(prev => ({ ...prev, netbox_token: event.target.value }))} /></div>
          </div>
        </div>

        <div className="section">
          <div className="section-header"><span>Observium</span>{statusTag(status.observium)}</div>
          <div className="section-body">
            <div className="field-row"><label>Base URL</label><input value={status.observium?.base_url || ""} readOnly /></div>
            <div className="field-row"><label>Mode</label><input value="DB + CLI (centralized manual export)" readOnly /></div>
            <div className="field-row"><label>Known devices</label><input value={status.observium?.devices ?? ""} readOnly /></div>
          </div>
        </div>
      </div>

      <div className="flex-gap" style={{ marginBottom: 32 }}>
        <button className="btn-primary" onClick={saveCreds}>Save Credentials</button>
        <button className="btn-secondary" onClick={checkAll} disabled={checking}>{checking ? "Checking..." : "Test Connections"}</button>
        <button className="btn-secondary" onClick={reloadExportLogs}>Reload Export Logs</button>
      </div>

      <div className="section" style={{ marginBottom: 24 }}>
        <div className="section-header"><span>Visual Filters</span></div>
        <div className="section-body">
          <div className="grid-3">
            <div className="field-row">
              <label>Theme Preset</label>
              <select value={uiPrefs.theme} onChange={event => onUiPrefsChange(prev => ({ ...prev, theme: event.target.value }))}>
                {Object.entries(themePresets).map(([key, value]) => (
                  <option key={key} value={key}>{value.label}</option>
                ))}
              </select>
            </div>
            <div className="field-row">
              <label>Correlation Highlight</label>
              <select value={uiPrefs.correlationMode} onChange={event => onUiPrefsChange(prev => ({ ...prev, correlationMode: event.target.value }))}>
                <option value="all">Saved + Auto</option>
                <option value="saved">Saved Only</option>
                <option value="off">Off</option>
              </select>
            </div>
            <div className="field-row">
              <label>Highlight Intensity</label>
              <select value={uiPrefs.highlightIntensity} onChange={event => onUiPrefsChange(prev => ({ ...prev, highlightIntensity: event.target.value }))}>
                <option value="strong">Strong</option>
                <option value="subtle">Subtle</option>
              </select>
            </div>
          </div>
          <div className="notice notice-info" style={{ marginBottom: 0 }}>
            Inventory now highlights saved correlation groups and automatic matches by IP/name based on the selected visual mode.
          </div>
        </div>
      </div>

      <div className="section" style={{ marginBottom: 24 }}>
        <div className="section-header">
          <span>Field Mapping</span>
          <button className="btn-primary" style={{ padding: "2px 10px", fontSize: 10 }} onClick={saveMapping}>Save</button>
        </div>
        <div className="section-body">
          {mappingError && <div className="notice notice-err" style={{ marginBottom: 8 }}>JSON Error: {mappingError}</div>}
          <textarea value={mapping} onChange={event => { setMapping(event.target.value); setMappingError(null); }} style={{ height: 220, resize: "vertical" }} />
        </div>
      </div>

      <div className="section">
        <div className="section-header"><span>Manual Export Log</span></div>
        <div className="section-body">
          {exportLogs.length === 0 && <div style={{ color: "var(--text3)" }}>No manual export logs yet.</div>}
          {exportLogs.length > 0 && (
            <div className="json-preview" style={{ maxHeight: 320, overflow: "auto" }}>
              {JSON.stringify(exportLogs, null, 2)}
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
