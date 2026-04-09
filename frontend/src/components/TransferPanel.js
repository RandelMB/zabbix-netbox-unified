import { useEffect, useMemo, useState } from "react";
import { useLogs } from "../hooks/useLogs";
import { api } from "../utils/api";

function buildCorrelationPayload(zabbixData, netboxData, observiumData, label) {
  const payload = { label: label || undefined };
  if (zabbixData?.hostid) payload.zabbix = { id: String(zabbixData.hostid), label: zabbixData.host || zabbixData.name || String(zabbixData.hostid) };
  if (netboxData?.id) payload.netbox = { id: String(netboxData.id), label: netboxData.name || String(netboxData.id) };
  if (observiumData?.device_id) payload.observium = { id: String(observiumData.device_id), label: observiumData.hostname || observiumData.sysName || String(observiumData.device_id) };
  return payload;
}

function correlationLabel(data) {
  if (!data) return "No linked devices";
  return data.label || Object.values(data.items || {}).map(item => item.label || item.id).filter(Boolean).join(" | ") || `Group ${data.id}`;
}

export function TransferPanel({ zabbixData, netboxData, observiumData }) {
  const { addLog } = useLogs();
  const [saving, setSaving] = useState(false);
  const [correlation, setCorrelation] = useState(null);
  const [correlationLabelInput, setCorrelationLabelInput] = useState("");

  const correlationPayload = useMemo(
    () => buildCorrelationPayload(zabbixData, netboxData, observiumData, correlationLabelInput),
    [zabbixData, netboxData, observiumData, correlationLabelInput]
  );

  const loadedSources = useMemo(
    () => ["zabbix", "netbox", "observium"].filter(source => {
      if (source === "zabbix") return !!zabbixData?.hostid;
      if (source === "netbox") return !!netboxData?.id;
      return !!observiumData?.device_id;
    }),
    [zabbixData, netboxData, observiumData]
  );

  useEffect(() => {
    let cancelled = false;

    async function loadCorrelation() {
      const candidates = [
        zabbixData?.hostid ? { source: "zabbix", id: zabbixData.hostid } : null,
        netboxData?.id ? { source: "netbox", id: netboxData.id } : null,
        observiumData?.device_id ? { source: "observium", id: observiumData.device_id } : null,
      ].filter(Boolean);

      if (!candidates.length) {
        setCorrelation(null);
        setCorrelationLabelInput("");
        return;
      }

      for (const candidate of candidates) {
        try {
          const response = await api.correlationMatch(candidate.source, candidate.id);
          if (cancelled) return;
          if (response.result) {
            setCorrelation(response.result);
            setCorrelationLabelInput(response.result.label || "");
            return;
          }
        } catch (error) {
          if (!cancelled) addLog("err", `Correlation lookup failed: ${error.message}`);
          return;
        }
      }

      if (!cancelled) {
        setCorrelation(null);
        setCorrelationLabelInput("");
      }
    }

    loadCorrelation();
    return () => {
      cancelled = true;
    };
  }, [zabbixData?.hostid, netboxData?.id, observiumData?.device_id, addLog]);

  async function saveCorrelation() {
    if (loadedSources.length < 2) {
      addLog("err", "Load at least two platform records before linking them");
      return;
    }
    setSaving(true);
    try {
      const result = await api.saveCorrelation(correlationPayload);
      setCorrelation(result.result);
      setCorrelationLabelInput(result.result?.label || "");
      addLog("ok", `Correlation saved: ${correlationLabel(result.result)}`, result);
    } catch (error) {
      addLog("err", `Correlation save failed: ${error.message}`);
    }
    setSaving(false);
  }

  async function unlinkSource(source) {
    if (!correlation?.id) {
      addLog("err", "No correlation is active for the current selection");
      return;
    }
    setSaving(true);
    try {
      const result = await api.unlinkCorrelation(correlation.id, source);
      if (result.status === "deleted") {
        setCorrelation(null);
        setCorrelationLabelInput("");
      } else {
        setCorrelation(result.correlation || null);
        setCorrelationLabelInput(result.correlation?.label || "");
      }
      addLog("ok", `Correlation unlinked for ${source}`, result);
    } catch (error) {
      addLog("err", `Correlation unlink failed: ${error.message}`);
    }
    setSaving(false);
  }

  return (
    <div style={{ padding: 16 }}>
      <div className="notice notice-info" style={{ marginBottom: 16 }}>
        Workspace linking for the current device selection.
      </div>

      <div className="section" style={{ marginBottom: 16, padding: 14 }}>
        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", gap: 12, marginBottom: 10, flexWrap: "wrap" }}>
          <div>
            <div style={{ color: "var(--text3)", fontSize: 11, fontFamily: "var(--font-mono)" }}>CORRELATION GROUP</div>
            <div style={{ fontWeight: 600 }}>{correlationLabel(correlation)}</div>
          </div>
          <div style={{ color: "var(--text3)", fontSize: 11, fontFamily: "var(--font-mono)" }}>
            Loaded platforms: {loadedSources.join(", ") || "none"}
          </div>
        </div>

        <div className="flex-gap" style={{ marginBottom: 10, flexWrap: "wrap", alignItems: "center" }}>
          <input
            placeholder="Optional link label"
            value={correlationLabelInput}
            onChange={event => setCorrelationLabelInput(event.target.value)}
            style={{ minWidth: 240, padding: "6px 10px" }}
          />
          <button className="btn-primary" onClick={saveCorrelation} disabled={saving || loadedSources.length < 2}>
            {correlation?.id ? "Update Link" : "Link Current Selection"}
          </button>
          {correlation?.items?.zabbix && <button className="btn-secondary" onClick={() => unlinkSource("zabbix")} disabled={saving}>Unlink Zabbix</button>}
          {correlation?.items?.netbox && <button className="btn-secondary" onClick={() => unlinkSource("netbox")} disabled={saving}>Unlink NetBox</button>}
          {correlation?.items?.observium && <button className="btn-secondary" onClick={() => unlinkSource("observium")} disabled={saving}>Unlink Observium</button>}
        </div>

        <div className="json-preview" style={{ marginBottom: 0 }}>
          {JSON.stringify(correlation || correlationPayload, null, 2)}
        </div>
      </div>
    </div>
  );
}
