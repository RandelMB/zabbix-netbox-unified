import { useEffect, useState } from "react";
import "./styles/globals.css";
import { LogProvider } from "./hooks/useLogs";
import { DeviceListPage } from "./pages/DeviceListPage";
import { WorkspacePage } from "./pages/WorkspacePage";
import { SettingsPage } from "./pages/SettingsPage";
import { DiscoveryPage } from "./pages/DiscoveryPage";

const THEME_PRESETS = {
  midnight_ops: {
    label: "Midnight Ops",
    vars: {
      "--bg": "#0d0f12",
      "--bg2": "#131620",
      "--bg3": "#1a1e2a",
      "--bg4": "#202435",
      "--border": "#2a2f3f",
      "--border2": "#353b52",
      "--accent": "#00d4aa",
      "--accent2": "#0099ff",
      "--accent3": "#ff6b35",
      "--text": "#e8ecf4",
      "--text2": "#8892a4",
      "--text3": "#525d72",
      "--success": "#00d4aa",
      "--error": "#ff4757",
      "--warn": "#ffa94d",
    },
  },
  copper_grid: {
    label: "Copper Grid",
    vars: {
      "--bg": "#15110d",
      "--bg2": "#1d1712",
      "--bg3": "#281f19",
      "--bg4": "#322720",
      "--border": "#4a3527",
      "--border2": "#644935",
      "--accent": "#ff9b54",
      "--accent2": "#ffd166",
      "--accent3": "#7bd389",
      "--text": "#f5ecdf",
      "--text2": "#bda78e",
      "--text3": "#8d735c",
      "--success": "#7bd389",
      "--error": "#ff6b6b",
      "--warn": "#ffd166",
    },
  },
  signal_blue: {
    label: "Signal Blue",
    vars: {
      "--bg": "#09131c",
      "--bg2": "#0f1b27",
      "--bg3": "#152434",
      "--bg4": "#1b2d42",
      "--border": "#27415b",
      "--border2": "#335776",
      "--accent": "#6be3ff",
      "--accent2": "#2dd4ff",
      "--accent3": "#ffcb6b",
      "--text": "#edf7ff",
      "--text2": "#97b8cf",
      "--text3": "#64839a",
      "--success": "#61f2c2",
      "--error": "#ff6b7a",
      "--warn": "#ffcb6b",
    },
  },
  paper_light: {
    label: "Paper Light",
    vars: {
      "--bg": "#f2efe8",
      "--bg2": "#fbf8f2",
      "--bg3": "#ede8dc",
      "--bg4": "#e3dccd",
      "--border": "#c7bca8",
      "--border2": "#b3a58c",
      "--accent": "#1b7f6b",
      "--accent2": "#356ae6",
      "--accent3": "#c46a3a",
      "--text": "#1f2a37",
      "--text2": "#516072",
      "--text3": "#7f8a98",
      "--success": "#1b7f6b",
      "--error": "#c0392b",
      "--warn": "#c98900",
    },
  },
};

const DEFAULT_UI_PREFS = {
  theme: "midnight_ops",
  correlationMode: "all",
  highlightIntensity: "strong",
};

function loadUiPrefs() {
  try {
    const parsed = JSON.parse(window.localStorage.getItem("zneditor_ui_prefs") || "{}");
    return { ...DEFAULT_UI_PREFS, ...parsed };
  } catch {
    return DEFAULT_UI_PREFS;
  }
}

function applyTheme(themeId, highlightIntensity) {
  const preset = THEME_PRESETS[themeId] || THEME_PRESETS.midnight_ops;
  Object.entries(preset.vars).forEach(([key, value]) => {
    document.documentElement.style.setProperty(key, value);
  });
  const strong = highlightIntensity !== "subtle";
  document.documentElement.style.setProperty("--correlation-saved-bg", strong ? "rgba(0, 212, 170, 0.18)" : "rgba(0, 212, 170, 0.08)");
  document.documentElement.style.setProperty("--correlation-auto-bg", strong ? "rgba(255, 172, 48, 0.18)" : "rgba(255, 172, 48, 0.08)");
  document.documentElement.style.setProperty("--correlation-saved-border", strong ? "rgba(0, 212, 170, 0.95)" : "rgba(0, 212, 170, 0.6)");
  document.documentElement.style.setProperty("--correlation-auto-border", strong ? "rgba(255, 172, 48, 0.95)" : "rgba(255, 172, 48, 0.6)");
}

function AppInner() {
  const [page, setPage] = useState("inventory");
  const [pendingTab, setPendingTab] = useState(null);
  const [uiPrefs, setUiPrefs] = useState(loadUiPrefs);

  useEffect(() => {
    applyTheme(uiPrefs.theme, uiPrefs.highlightIntensity);
    window.localStorage.setItem("zneditor_ui_prefs", JSON.stringify(uiPrefs));
  }, [uiPrefs]);

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
          { id: "discovery", label: "◎ Discovery" },
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
          <DeviceListPage onOpenDevice={openDevice} active={page === "inventory"} uiPrefs={uiPrefs} />
        </div>
        <div style={{ display: page === "workspace" ? "flex" : "none", height: "100%", flexDirection: "column" }}>
          <WorkspacePage pendingTab={page === "workspace" ? pendingTab : null} onPendingConsumed={() => setPendingTab(null)} />
        </div>
        <div style={{ display: page === "discovery" ? "block" : "none", height: "100%", overflow: "auto" }}>
          <DiscoveryPage active={page === "discovery"} />
        </div>
        <div style={{ display: page === "settings" ? "block" : "none", height: "100%", overflow: "auto" }}>
          <SettingsPage uiPrefs={uiPrefs} onUiPrefsChange={setUiPrefs} themePresets={THEME_PRESETS} />
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
