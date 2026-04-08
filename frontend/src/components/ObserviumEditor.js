import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { api } from "../utils/api";
import { useLogs } from "../hooks/useLogs";
import { ConfirmModal } from "./ConfirmModal";

const TABS = ["General", "SNMP", "Raw JSON"];

const EMPTY_DEVICE = {
  hostname: "",
  ip: "",
  label: "",
  snmp_version: "v2c",
  snmp_community: "",
  snmp_port: 161,
  snmp_transport: "udp",
  location: "",
  purpose: "",
  skip_icmp: false,
  disabled: false,
  ignore: false,
};

function buildSnmpCommand(device) {
  if (!device.hostname || !device.snmp_community) return "";
  return `snmpget -Oqv -On -t 3 -r 1 -v ${device.snmp_version} -c '${device.snmp_community}' ${device.hostname}:${device.snmp_port || 161} 1.3.6.1.2.1.1.2.0`;
}

export function ObserviumEditor({ deviceId, onDataReady }) {
  const { addLog } = useLogs();
  const onDataReadyRef = useRef(onDataReady);
  const isCreateMode = deviceId === "new";
  const [loading, setLoading] = useState(!isCreateMode);
  const [device, setDevice] = useState(EMPTY_DEVICE);
  const [tab, setTab] = useState("General");
  const [confirm, setConfirm] = useState(null);
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    onDataReadyRef.current = onDataReady;
  }, [onDataReady]);

  const load = useCallback(async () => {
    if (isCreateMode) {
      setDevice(EMPTY_DEVICE);
      setLoading(false);
      return;
    }

    setLoading(true);
    try {
      const response = await api.observiumDevice(deviceId);
      setDevice(response.result);
      if (onDataReadyRef.current) onDataReadyRef.current(response.result);
      addLog("ok", `Loaded Observium device: ${response.result.hostname}`);
    } catch (error) {
      addLog("err", "Failed to load Observium device: " + error.message);
    }
    setLoading(false);
  }, [addLog, deviceId, isCreateMode]);

  useEffect(() => {
    load();
  }, [load]);

  const previewPayload = useMemo(
    () => ({
      hostname: device.hostname,
      ip: device.ip,
      label: device.label,
      snmp_version: device.snmp_version,
      snmp_community: device.snmp_community,
      snmp_port: Number(device.snmp_port || 161),
      snmp_transport: device.snmp_transport,
      location: device.location,
      purpose: device.purpose,
      skip_icmp: !!device.skip_icmp,
      disabled: !!device.disabled,
      ignore: !!device.ignore,
    }),
    [device]
  );

  function updateField(field, value) {
    setDevice(prev => ({ ...prev, [field]: value }));
  }

  async function handleCreate(payload) {
    setSaving(true);
    try {
      const response = await api.observiumCreateDevice(payload);
      addLog("ok", `Observium device created: ${response.hostname}`, response);
      setConfirm(null);
      setDevice(prev => ({ ...prev, device_id: response.device_id }));
    } catch (error) {
      addLog("err", "Observium create failed: " + error.message);
    }
    setSaving(false);
  }

  async function handleSave(payload) {
    setSaving(true);
    try {
      const response = await api.observiumUpdateDevice(device.device_id, payload);
      addLog("ok", `Observium device updated: ${response.result.hostname}`, response);
      setConfirm(null);
      await load();
    } catch (error) {
      addLog("err", "Observium update failed: " + error.message);
    }
    setSaving(false);
  }

  async function handleRefresh() {
    if (!device.device_id) return;
    setSaving(true);
    try {
      const response = await api.observiumRefreshDevice(device.device_id);
      addLog("ok", `Observium poll/discovery executed: ${response.hostname}`, response);
      await load();
    } catch (error) {
      addLog("err", "Observium refresh failed: " + error.message);
    }
    setSaving(false);
  }

  async function handleDelete() {
    if (!device.device_id || !window.confirm(`Delete ${device.hostname} from Observium?`)) return;
    setSaving(true);
    try {
      await api.observiumDeleteDevice(device.device_id);
      addLog("ok", `Observium device deleted: ${device.hostname}`);
      setDevice(EMPTY_DEVICE);
    } catch (error) {
      addLog("err", "Observium delete failed: " + error.message);
    }
    setSaving(false);
  }

  async function toggleArchiveSelf() {
    if (isCreateMode || !device.device_id) return;
    try {
      if (device.archived) {
        await api.restoreDevice("observium", device.device_id);
        addLog("ok", `Observium device restored: ${device.hostname}`);
      } else {
        await api.archiveDevice("observium", device.device_id, { label: device.hostname });
        addLog("ok", `Observium device archived: ${device.hostname}`);
      }
      await load();
    } catch (error) {
      addLog("err", "Archive toggle failed: " + error.message);
    }
  }

  if (loading) return <div style={{ padding: 20, color: "var(--text3)" }}>Loading...</div>;

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
        <span className="tag" style={{ background: "rgba(255,172,48,0.18)", color: "#ffac30", borderColor: "rgba(255,172,48,0.45)" }}>OBS</span>
        <span style={{ fontFamily: "var(--font-mono)", fontWeight: 600, fontSize: 14 }}>
          {isCreateMode ? "New Observium Device" : (device.hostname || "Observium")}
        </span>
        {!isCreateMode && (
          <span style={{ color: "var(--text3)", fontSize: 12 }}>
            {device.sysName || device.ip || ""}
          </span>
        )}
        {!isCreateMode && (
          <span style={{ marginLeft: "auto", display: "flex", gap: 8 }}>
            <span className={`tag ${device.disabled ? "tag-warn" : "tag-ok"}`}>{device.disabled ? "disabled" : "enabled"}</span>
          </span>
        )}
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
                <label>Hostname / Poll Target</label>
                <input value={device.hostname || ""} onChange={e => updateField("hostname", e.target.value)} />
              </div>
              <div className="field-row">
                <label>IP</label>
                <input value={device.ip || ""} onChange={e => updateField("ip", e.target.value)} />
              </div>
              <div className="field-row">
                <label>Display Label</label>
                <input value={device.label || ""} onChange={e => updateField("label", e.target.value)} />
              </div>
              <div className="field-row">
                <label>Location</label>
                <input value={device.location || ""} onChange={e => updateField("location", e.target.value)} />
              </div>
              <div className="field-row">
                <label>Purpose</label>
                <input value={device.purpose || ""} onChange={e => updateField("purpose", e.target.value)} />
              </div>
              <div className="field-row">
                <label>Skip ICMP Echo Checks</label>
                <select value={device.skip_icmp ? "1" : "0"} onChange={e => updateField("skip_icmp", e.target.value === "1")}>
                  <option value="0">No</option>
                  <option value="1">Yes</option>
                </select>
              </div>
              <div className="field-row">
                <label>Transport</label>
                <select value={device.snmp_transport || "udp"} onChange={e => updateField("snmp_transport", e.target.value)}>
                  <option value="udp">udp</option>
                  <option value="udp6">udp6</option>
                  <option value="tcp">tcp</option>
                  <option value="tcp6">tcp6</option>
                </select>
              </div>
            </div>

            {!isCreateMode && (
              <div className="notice notice-info" style={{ marginTop: 12 }}>
                In Observium, hostname is the polling target. If you want a friendly display name without changing the SNMP target, use Display Label.
              </div>
            )}

            {!isCreateMode && (
              <div className="grid-2" style={{ marginTop: 8 }}>
                <div className="field-row">
                  <label>Disabled</label>
                  <select value={device.disabled ? "1" : "0"} onChange={e => updateField("disabled", e.target.value === "1")}>
                    <option value="0">No</option>
                    <option value="1">Yes</option>
                  </select>
                </div>
                <div className="field-row">
                  <label>Ignore</label>
                  <select value={device.ignore ? "1" : "0"} onChange={e => updateField("ignore", e.target.value === "1")}>
                    <option value="0">No</option>
                    <option value="1">Yes</option>
                  </select>
                </div>
              </div>
            )}
          </div>
        )}

        {tab === "SNMP" && (
          <div>
            <div className="grid-2">
              <div className="field-row">
                <label>SNMP Version</label>
                <select value={device.snmp_version || "v2c"} onChange={e => updateField("snmp_version", e.target.value)}>
                  <option value="v1">v1</option>
                  <option value="v2c">v2c</option>
                  <option value="v3">v3</option>
                </select>
              </div>
              <div className="field-row">
                <label>SNMP Port</label>
                <input value={device.snmp_port || 161} onChange={e => updateField("snmp_port", e.target.value)} />
              </div>
              <div className="field-row">
                <label>Community</label>
                <input value={device.snmp_community || ""} onChange={e => updateField("snmp_community", e.target.value)} />
              </div>
              <div className="field-row">
                <label>Base URL</label>
                <input value={device.web_url || ""} readOnly />
              </div>
            </div>

            <div className="field-row">
              <label>Validation Command</label>
              <textarea
                readOnly
                value={buildSnmpCommand(device) || "Complete hostname and community to build the snmpget command."}
                style={{ height: 74, resize: "vertical", fontFamily: "var(--font-mono)" }}
              />
            </div>
          </div>
        )}

        {tab === "Raw JSON" && (
          <div className="json-preview" style={{ height: "100%", minHeight: 360 }}>
            {JSON.stringify(device, null, 2)}
          </div>
        )}
      </div>

      {tab !== "Raw JSON" && (
        <div style={{ padding: "12px 16px", borderTop: "1px solid var(--border)", display: "flex", gap: 8, flexWrap: "wrap" }}>
          {isCreateMode ? (
            <button
              className="btn-primary"
              onClick={() => setConfirm({
                title: `Create Observium Device: ${device.hostname || "new-device"}`,
                payload: {
                  hostname: device.hostname,
                  snmp_version: device.snmp_version,
                  snmp_community: device.snmp_community,
                  snmp_port: Number(device.snmp_port || 161),
                  snmp_transport: device.snmp_transport,
                  skip_icmp: !!device.skip_icmp,
                  run_discovery: true,
                  run_poller: true,
                },
                onConfirm: handleCreate,
              })}
            >
              Preview & Create
            </button>
          ) : (
            <>
              <button
                className="btn-primary"
                onClick={() => setConfirm({
                  title: `Update Observium Device: ${device.hostname}`,
                  payload: previewPayload,
                  onConfirm: handleSave,
                })}
              >
                Preview & Save
              </button>
              <button className="btn-secondary" onClick={handleRefresh} disabled={saving}>Run Poller/Discovery</button>
              <button className="btn-danger" onClick={handleDelete} disabled={saving}>Delete</button>
              <button className="btn-secondary" onClick={toggleArchiveSelf} disabled={saving}>{device.archived ? "Restore" : "Archive"}</button>
            </>
          )}
          <button className="btn-secondary" onClick={load}>Reload</button>
        </div>
      )}
    </div>
  );
}
