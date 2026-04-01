import { useState } from "react";
import "./styles/globals.css";
import { LogProvider } from "./hooks/useLogs";
import { DeviceListPage } from "./pages/DeviceListPage";
import { WorkspacePage } from "./pages/WorkspacePage";
import { SettingsPage } from "./pages/SettingsPage";

function AppInner() {
  const [page, setPage] = useState("inventory");
  const [pendingTab, setPendingTab] = useState(null);

  function openDevice(device) {
    setPendingTab(device);
    setPage("workspace");
  }

  return (
    <div style={{ display: "flex", flexDirection: "column", height: "100vh", overflow: "hidden" }}>
      <nav
        style={{
          display: "flex",
          alignItems: "center",
          background: "var(--bg2)",
          borderBottom: "1px solid var(--border)",
          padding: "0 20px",
          height: 48,
          flexShrink: 0,
          gap: 0,
        }}
      >
        <div style={{ display: "flex", alignItems: "center", gap: 10, marginRight: 36 }}>
          <div
            style={{
              width: 30,
              height: 30,
              borderRadius: 6,
              background: "linear-gradient(135deg, #d40000 0%, #0099ff 100%)",
              display: "flex",
              alignItems: "center",
              justifyContent: "center",
              fontSize: 16,
              fontWeight: 900,
              color: "#fff",
            }}
          >
            ⇄
          </div>
          <div>
            <div style={{ fontFamily: "var(--font-mono)", fontWeight: 700, fontSize: 13, color: "var(--text)", lineHeight: 1.2 }}>
              ZN<span style={{ color: "var(--accent)" }}>Editor</span>
            </div>
            <div style={{ fontFamily: "var(--font-mono)", fontSize: 9, color: "var(--text3)", letterSpacing: "0.1em" }}>
              ZABBIX · NETBOX · OBSERVIUM
            </div>
          </div>
        </div>

        {[
          { id: "inventory", label: "⊞ Inventory" },
          { id: "workspace", label: "⊟ Workspace" },
          { id: "settings", label: "⚙ Settings" },
        ].map(item => (
          <button
            key={item.id}
            onClick={() => setPage(item.id)}
            style={{
              background: "transparent",
              border: "none",
              padding: "0 18px",
              height: 48,
              color: page === item.id ? "var(--accent)" : "var(--text2)",
              borderBottom: page === item.id ? "2px solid var(--accent)" : "2px solid transparent",
              fontFamily: "var(--font-sans)",
              fontSize: 13,
              fontWeight: 500,
              cursor: "pointer",
              transition: "all 0.12s",
            }}
          >
            {item.label}
          </button>
        ))}

        <div style={{ marginLeft: "auto", display: "flex", gap: 6, alignItems: "center" }}>
          <span className="tag tag-zabbix" style={{ fontSize: 10 }}>Zabbix</span>
          <span style={{ color: "var(--text3)", fontSize: 12 }}>⇄</span>
          <span className="tag tag-netbox" style={{ fontSize: 10 }}>NetBox</span>
          <span style={{ color: "var(--text3)", fontSize: 12 }}>⇄</span>
          <span className="tag" style={{ fontSize: 10, background: "rgba(255,172,48,0.18)", color: "#ffac30", borderColor: "rgba(255,172,48,0.45)" }}>Observium</span>
          <span style={{ marginLeft: 8, fontFamily: "var(--font-mono)", fontSize: 9, color: "var(--text3)" }}>
            manual control
          </span>
        </div>
      </nav>

      <div style={{ flex: 1, overflow: "hidden" }}>
        <div style={{ display: page === "inventory" ? "block" : "none", height: "100%", overflow: "hidden" }}>
          <DeviceListPage onOpenDevice={openDevice} />
        </div>
        <div style={{ display: page === "workspace" ? "flex" : "none", height: "100%", flexDirection: "column" }}>
          <WorkspacePage pendingTab={page === "workspace" ? pendingTab : null} onPendingConsumed={() => setPendingTab(null)} />
        </div>
        <div style={{ display: page === "settings" ? "block" : "none", height: "100%", overflow: "auto" }}>
          <SettingsPage />
        </div>
      </div>
    </div>
  );
}

export default function App() {
  return (
    <LogProvider>
      <AppInner />
    </LogProvider>
  );
}
