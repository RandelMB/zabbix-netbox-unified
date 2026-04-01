import { useEffect, useMemo, useState } from "react";
import { useLogs } from "../hooks/useLogs";
import { api } from "../utils/api";
import { ConfirmModal } from "./ConfirmModal";

const DIRECTIONS = [
  { id: "z2n", label: "Zabbix -> NetBox" },
  { id: "n2z", label: "NetBox -> Zabbix" },
  { id: "z2o", label: "Zabbix -> Observium" },
];

function getMainZabbixIp(host) {
  const mainIface = (host?.interfaces || []).find(item => item.main === "1") || host?.interfaces?.[0];
  return mainIface?.ip || mainIface?.dns || "";
}

function getNetBoxIp(device) {
  return device?.primary_ip4?.address || device?.primary_ip?.address || "";
}

function buildZ2NPayload(zhost) {
  if (!zhost) return {};
  const payload = {};
  if (zhost.host) payload.name = zhost.host;
  if (zhost.description) payload.comments = zhost.description;
  if (getMainZabbixIp(zhost)) payload._primary_ip_hint = getMainZabbixIp(zhost);
  return payload;
}

function buildN2ZPayload(nbDevice) {
  if (!nbDevice) return {};
  const payload = {};
  if (nbDevice.name) payload.host = nbDevice.name;
  if (nbDevice.comments) payload.description = nbDevice.comments;
  if (nbDevice.primary_ip4?.address) payload._interface_ip_hint = nbDevice.primary_ip4.address.split("/")[0];
  return payload;
}

function buildZ2OPayload(zhost) {
  if (!zhost) return {};
  const snmpIface = (zhost.interfaces || []).find(item => item.type === "2") || zhost.interfaces?.[0];
  return {
    hostids: zhost.hostid ? [zhost.hostid] : [],
    address: snmpIface?.ip || snmpIface?.dns || "",
    snmp_version: snmpIface?.details?.version || "",
    community: snmpIface?.details?.community || "",
  };
}

function buildCorrelationPayload(zabbixData, netboxData, observiumData, label) {
  const payload = { label: label || undefined };
  if (zabbixData?.hostid) payload.zabbix = { id: String(zabbixData.hostid), label: zabbixData.host || zabbixData.name || String(zabbixData.hostid) };
  if (netboxData?.id) payload.netbox = { id: String(netboxData.id), label: netboxData.name || String(netboxData.id) };
  if (observiumData?.device_id) payload.observium = { id: String(observiumData.device_id), label: observiumData.hostname || observiumData.sysName || String(observiumData.device_id) };
  return payload;
}

function comparisonRows(zabbixData, netboxData, observiumData) {
  return [
    { field: "Name", zabbix: zabbixData?.host || "-", netbox: netboxData?.name || "-", observium: observiumData?.hostname || "-" },
    { field: "IP", zabbix: getMainZabbixIp(zabbixData) || "-", netbox: getNetBoxIp(netboxData) || "-", observium: observiumData?.ip || "-" },
    { field: "Location", zabbix: zabbixData?.inventory?.location || "-", netbox: netboxData?.site?.name || "-", observium: observiumData?.location || "-" },
    { field: "SNMP", zabbix: zabbixData?.interfaces?.find(item => item.type === "2")?.details?.community || "-", netbox: "-", observium: observiumData?.snmp_community || "-" },
  ];
}

function correlationLabel(data) {
  if (!data) return "No linked devices";
  return data.label || Object.values(data.items || {}).map(item => item.label || item.id).filter(Boolean).join(" | ") || `Group ${data.id}`;
}

export function TransferPanel({ zabbixData, netboxData, observiumData }) {
  const { addLog } = useLogs();
  const [direction, setDirection] = useState("z2n");
  const [mapping, setMapping] = useState(null);
  const [preview, setPreview] = useState(null);
  const [confirm, setConfirm] = useState(null);
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
    setPreview(null);
  }, [zabbixData, netboxData, observiumData, direction]);

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

  async function loadMapping() {
    const result = await api.getMapping();
    setMapping(result);
  }

  function buildPreview() {
    if (direction === "z2n") {
      setPreview({ direction, payload: buildZ2NPayload(zabbixData) });
      return;
    }
    if (direction === "n2z") {
      setPreview({ direction, payload: buildN2ZPayload(netboxData) });
      return;
    }
    setPreview({ direction, payload: buildZ2OPayload(zabbixData) });
  }

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

  async function executePreview() {
    if (direction === "z2n" && netboxData?.id) {
      setConfirm({
        title: `Transfer Zabbix -> NetBox: ${netboxData.name}`,
        payload: preview.payload,
        onConfirm: async payload => {
          setSaving(true);
          try {
            const result = await api.netboxUpdateDevice(netboxData.id, payload);
            addLog("ok", `Transferred to NetBox: ${netboxData.name}`, result);
            setConfirm(null);
          } catch (error) {
            addLog("err", error.message);
          }
          setSaving(false);
        },
      });
      return;
    }

    if (direction === "n2z" && zabbixData?.hostid) {
      setConfirm({
        title: `Transfer NetBox -> Zabbix: ${zabbixData.host}`,
        payload: preview.payload,
        onConfirm: async payload => {
          setSaving(true);
          try {
            const result = await api.zabbixUpdateHost(zabbixData.hostid, payload);
            addLog("ok", `Transferred to Zabbix: ${zabbixData.host}`, result);
            setConfirm(null);
          } catch (error) {
            addLog("err", error.message);
          }
          setSaving(false);
        },
      });
      return;
    }

    if (direction === "z2o" && zabbixData?.hostid) {
      setConfirm({
        title: `Export Zabbix -> Observium: ${zabbixData.host}`,
        payload: preview.payload,
        onConfirm: async () => {
          setSaving(true);
          try {
            const result = await api.exportZabbixToObservium({ hostids: [zabbixData.hostid], run_discovery: true, run_poller: true, update_existing: true });
            addLog("ok", `Exported to Observium: ${zabbixData.host}`, result);
            setConfirm(null);
          } catch (error) {
            addLog("err", error.message);
          }
          setSaving(false);
        },
      });
      return;
    }

    addLog("err", "Target device not loaded in workspace");
  }

  return (
    <div style={{ padding: 16 }}>
      {confirm && (
        <ConfirmModal
          title={confirm.title}
          payload={confirm.payload}
          loading={saving}
          onCancel={() => setConfirm(null)}
          onConfirm={confirm.onConfirm}
        />
      )}

      <div className="notice notice-info" style={{ marginBottom: 16 }}>
        Workspace comparison across the three platforms. Manual exports to Observium run only from this application.
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

      <table style={{ marginBottom: 16 }}>
        <thead>
          <tr><th>Field</th><th>Zabbix</th><th>NetBox</th><th>Observium</th></tr>
        </thead>
        <tbody>
          {comparisonRows(zabbixData, netboxData, observiumData).map(row => (
            <tr key={row.field}>
              <td>{row.field}</td>
              <td>{row.zabbix}</td>
              <td>{row.netbox}</td>
              <td>{row.observium}</td>
            </tr>
          ))}
        </tbody>
      </table>

      <div className="flex-gap" style={{ marginBottom: 12, flexWrap: "wrap" }}>
        {DIRECTIONS.map(item => (
          <button key={item.id} className={direction === item.id ? "btn-primary" : "btn-secondary"} onClick={() => setDirection(item.id)}>
            {item.label}
          </button>
        ))}
        <button className="btn-secondary" onClick={loadMapping}>Load Mapping</button>
        <button className="btn-secondary" onClick={buildPreview}>Build Preview</button>
      </div>

      {preview && (
        <div>
          <div style={{ marginBottom: 8, color: "var(--text3)", fontSize: 11, fontFamily: "var(--font-mono)" }}>
            TRANSFER PREVIEW
          </div>
          <div className="json-preview" style={{ marginBottom: 16 }}>
            {JSON.stringify(preview.payload, null, 2)}
          </div>
          <button className="btn-primary" onClick={executePreview}>Preview & Execute</button>
        </div>
      )}

      {mapping && (
        <div style={{ marginTop: 20 }}>
          <div style={{ color: "var(--text3)", fontSize: 11, marginBottom: 6, fontFamily: "var(--font-mono)" }}>ACTIVE FIELD MAPPING</div>
          <div className="json-preview">{JSON.stringify(mapping, null, 2)}</div>
        </div>
      )}
    </div>
  );
}
