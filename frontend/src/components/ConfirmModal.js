import { useState } from "react";

export function ConfirmModal({ title, payload, onConfirm, onCancel, loading }) {
  const [jsonStr, setJsonStr] = useState(JSON.stringify(payload, null, 2));
  const [parseError, setParseError] = useState(null);

  function handleChange(v) {
    setJsonStr(v);
    try { JSON.parse(v); setParseError(null); }
    catch (e) { setParseError(e.message); }
  }

  function handleConfirm() {
    try {
      const parsed = JSON.parse(jsonStr);
      onConfirm(parsed);
    } catch (e) {
      setParseError(e.message);
    }
  }

  return (
    <div className="modal-overlay">
      <div className="modal" style={{ minWidth: 620 }}>
        <div className="flex justify-between align-center" style={{ marginBottom: 16 }}>
          <h3 style={{ margin: 0, color: "var(--warn)" }}>⚠ Preview & Confirm</h3>
          <button className="btn-icon" onClick={onCancel}>✕</button>
        </div>
        <p style={{ color: "var(--text2)", marginBottom: 12, fontSize: 12 }}>
          <strong style={{ color: "var(--text)" }}>{title}</strong> — Review the exact payload below.
          You can edit it before sending.
        </p>
        {parseError && (
          <div className="notice notice-err" style={{ marginBottom: 8 }}>JSON Error: {parseError}</div>
        )}
        <textarea
          value={jsonStr}
          onChange={e => handleChange(e.target.value)}
          style={{ height: 320, resize: "vertical", fontFamily: "var(--font-mono)", fontSize: 11 }}
        />
        <div className="flex-gap" style={{ marginTop: 16, justifyContent: "flex-end" }}>
          <button className="btn-secondary" onClick={onCancel}>Cancel</button>
          <button
            className="btn-primary"
            onClick={handleConfirm}
            disabled={!!parseError || loading}
          >
            {loading ? "Sending…" : "✓ Confirm & Execute"}
          </button>
        </div>
      </div>
    </div>
  );
}
