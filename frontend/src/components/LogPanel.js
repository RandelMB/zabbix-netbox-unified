import { useState } from "react";
import { useLogs } from "../hooks/useLogs";

export function LogPanel() {
  const { logs, clearLogs } = useLogs();
  const [expanded, setExpanded] = useState(null);

  return (
    <div style={{ height: "100%", display: "flex", flexDirection: "column" }}>
      <div className="section-header">
        <span>Activity Log ({logs.length})</span>
        <button className="btn-secondary" style={{ padding: "2px 8px", fontSize: 10 }} onClick={clearLogs}>Clear</button>
      </div>
      <div style={{ flex: 1, overflow: "auto" }}>
        {logs.length === 0 && (
          <div style={{ padding: 20, color: "var(--text3)", textAlign: "center", fontFamily: "var(--font-mono)", fontSize: 11 }}>
            No activity yet
          </div>
        )}
        {logs.map(log => (
          <div key={log.id} className="log-entry" style={{ cursor: log.detail ? "pointer" : "default" }}
            onClick={() => setExpanded(expanded === log.id ? null : log.id)}>
            <span className="log-ts">{log.ts}</span>
            <span className={`log-${log.type}`}>
              {log.type === "ok" ? "✓" : log.type === "err" ? "✗" : "→"} {log.message}
            </span>
            {log.detail && expanded === log.id && (
              <div className="json-preview" style={{ marginTop: 8, fontSize: 10 }}>
                {typeof log.detail === "string" ? log.detail : JSON.stringify(log.detail, null, 2)}
              </div>
            )}
          </div>
        ))}
      </div>
    </div>
  );
}
