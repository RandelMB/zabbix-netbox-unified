import { useEffect, useMemo, useState } from "react";
import { ZabbixEditor } from "../components/ZabbixEditor";
import { NetBoxEditor } from "../components/NetBoxEditor";
import { ObserviumEditor } from "../components/ObserviumEditor";
import { TransferPanel } from "../components/TransferPanel";
import { LogPanel } from "../components/LogPanel";

let _tabCounter = 1;

const PANEL_META = {
  zabbix: { label: "Zabbix", short: "Z", className: "tag-zabbix", empty: "Open a Zabbix host from Inventory" },
  netbox: { label: "NetBox", short: "NB", className: "tag-netbox", empty: "Open a NetBox device from Inventory" },
  observium: { label: "Observium", short: "OBS", className: "", empty: "Open or create an Observium device from Inventory", style: { background: "rgba(255,172,48,0.18)", color: "#ffac30", borderColor: "rgba(255,172,48,0.45)" } },
};

export function WorkspacePage({ pendingTab, onPendingConsumed }) {
  const [tabs, setTabs] = useState([]);
  const [activeByType, setActiveByType] = useState({ zabbix: null, netbox: null, observium: null });
  const [showLog, setShowLog] = useState(true);
  const [showTransfer, setShowTransfer] = useState(true);
  const [deviceData, setDeviceData] = useState({});

  useEffect(() => {
    if (pendingTab) {
      if (Array.isArray(pendingTab?.devices)) {
        addTabs(pendingTab.devices);
      } else {
        addTabs([pendingTab]);
      }
      onPendingConsumed && onPendingConsumed();
    }
  }, [pendingTab, onPendingConsumed]);

  function addTabs(devices) {
    const nextActive = {};
    setTabs(prev => {
      const next = [...prev];
      for (const device of devices) {
        if (!device?.type || device?.id === undefined || device?.id === null) continue;
        const exists = next.find(item => item.type === device.type && item.deviceId === device.id);
        if (exists) {
          nextActive[device.type] = exists.id;
          continue;
        }
        const id = ++_tabCounter;
        next.push({ id, type: device.type, label: device.label, deviceId: device.id });
        nextActive[device.type] = id;
      }
      return next;
    });
    setTimeout(() => setActiveByType(current => ({ ...current, ...nextActive })), 0);
  }

  function openNewTab(type) {
    const labels = {
      zabbix: "New Zabbix SNMP Host",
      netbox: "New NetBox Device",
      observium: "New Observium Device",
    };
    addTabs([{ type, id: "new", label: labels[type] || "New Device" }]);
  }

  function closeTab(id) {
    setTabs(prev => {
      const current = prev.find(item => item.id === id);
      const next = prev.filter(item => item.id !== id);
      if (current && activeByType[current.type] === id) {
        const replacement = next.find(item => item.type === current.type);
        setActiveByType(state => ({ ...state, [current.type]: replacement ? replacement.id : null }));
      }
      return next;
    });
    setDeviceData(prev => {
      const clone = { ...prev };
      delete clone[id];
      return clone;
    });
  }

  function handleDataReady(tabId, data) {
    setDeviceData(prev => ({ ...prev, [tabId]: data }));
  }

  const tabsByType = useMemo(
    () => ({
      zabbix: tabs.filter(item => item.type === "zabbix"),
      netbox: tabs.filter(item => item.type === "netbox"),
      observium: tabs.filter(item => item.type === "observium"),
    }),
    [tabs]
  );

  const selected = {
    zabbix: deviceData[activeByType.zabbix] || null,
    netbox: deviceData[activeByType.netbox] || null,
    observium: deviceData[activeByType.observium] || null,
  };

  function renderEditor(type) {
    const activeId = activeByType[type];
    const tab = tabs.find(item => item.id === activeId);
    if (!tab) {
      return (
        <div style={{ flex: 1, display: "flex", alignItems: "center", justifyContent: "center", color: "var(--text3)", padding: 20, textAlign: "center" }}>
          {PANEL_META[type].empty}
        </div>
      );
    }

    if (type === "zabbix") {
      return <ZabbixEditor key={`z-${tab.deviceId}`} hostId={tab.deviceId} onDataReady={data => handleDataReady(tab.id, data)} />;
    }
    if (type === "netbox") {
      return <NetBoxEditor key={`n-${tab.deviceId}`} deviceId={tab.deviceId} onDataReady={data => handleDataReady(tab.id, data)} />;
    }
    return <ObserviumEditor key={`o-${tab.deviceId}`} deviceId={tab.deviceId} onDataReady={data => handleDataReady(tab.id, data)} />;
  }

  return (
    <div style={{ display: "flex", flexDirection: "column", height: "100%", minHeight: 0 }}>
      <div style={{ display: "flex", gap: 6, padding: "10px 16px", borderBottom: "1px solid var(--border)", background: "var(--bg2)", flexShrink: 0 }}>
        <button className="btn-secondary" onClick={() => setShowTransfer(prev => !prev)}>{showTransfer ? "Hide Compare" : "Show Compare"}</button>
        <button className="btn-secondary" onClick={() => setShowLog(prev => !prev)}>{showLog ? "Hide Log" : "Show Log"}</button>
      </div>

      {showTransfer && (
        <div style={{ borderBottom: "1px solid var(--border)", background: "var(--bg2)", flexShrink: 0, maxHeight: 220, overflow: "auto" }}>
          <div className="section-header">
            <span>Central Workspace Control</span>
          </div>
          <TransferPanel zabbixData={selected.zabbix} netboxData={selected.netbox} observiumData={selected.observium} />
        </div>
      )}

      <div style={{ flex: 1, display: "flex", overflow: "hidden", minHeight: 0 }}>
        <div style={{ flex: 1, display: "grid", gridTemplateColumns: "repeat(3, minmax(0, 1fr))", overflow: "hidden", minHeight: 0 }}>
          {["zabbix", "netbox", "observium"].map(type => (
            <div key={type} style={{ display: "flex", flexDirection: "column", minWidth: 0, minHeight: 0, borderRight: type !== "observium" ? "1px solid var(--border)" : "none" }}>
              <div style={{ padding: "10px 12px", borderBottom: "1px solid var(--border)", background: "var(--bg2)", display: "flex", alignItems: "center", gap: 8, flexShrink: 0 }}>
                <span className={`tag ${PANEL_META[type].className}`} style={{ ...(PANEL_META[type].style || {}) }}>{PANEL_META[type].short}</span>
                <strong style={{ fontSize: 12 }}>{PANEL_META[type].label}</strong>
                <button
                  className="btn-secondary"
                  style={{ marginLeft: "auto", padding: "4px 10px", fontSize: 10 }}
                  onClick={() => openNewTab(type)}
                >
                  + New
                </button>
              </div>
              <div style={{ padding: "6px 8px", borderBottom: "1px solid var(--border)", background: "var(--bg3)", display: "flex", gap: 6, overflowX: "auto", flexShrink: 0 }}>
                {tabsByType[type].length === 0 && <span style={{ color: "var(--text3)", fontSize: 11 }}>No device loaded</span>}
                {tabsByType[type].map(tab => (
                  <div
                    key={tab.id}
                    style={{
                      display: "flex",
                      alignItems: "center",
                      gap: 6,
                      padding: "4px 8px",
                      border: activeByType[type] === tab.id ? "1px solid var(--accent)" : "1px solid var(--border)",
                      borderRadius: "var(--radius)",
                      background: activeByType[type] === tab.id ? "rgba(0,212,170,0.08)" : "var(--bg2)",
                      minWidth: 0,
                    }}
                  >
                    <button style={{ background: "transparent", border: "none", color: "var(--text)", cursor: "pointer", padding: 0, fontSize: 11 }} onClick={() => setActiveByType(prev => ({ ...prev, [type]: tab.id }))}>
                      {tab.label}
                    </button>
                    <button style={{ background: "transparent", border: "none", color: "var(--text3)", cursor: "pointer", padding: 0, fontSize: 11 }} onClick={() => closeTab(tab.id)}>✕</button>
                  </div>
                ))}
              </div>
              <div style={{ flex: 1, minHeight: 0, overflow: "hidden" }}>
                {renderEditor(type)}
              </div>
            </div>
          ))}
        </div>
        {showLog && (
          <div style={{ width: 320, flexShrink: 0, borderLeft: "1px solid var(--border)", background: "var(--bg2)", overflow: "hidden", minHeight: 0 }}>
            <LogPanel />
          </div>
        )}
      </div>
    </div>
  );
}
