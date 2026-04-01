import { createContext, useContext, useState, useCallback } from "react";

const LogCtx = createContext(null);

export function LogProvider({ children }) {
  const [logs, setLogs] = useState([]);

  const addLog = useCallback((type, message, detail = null) => {
    setLogs(prev => [{
      id: Date.now() + Math.random(),
      ts: new Date().toISOString().slice(11, 19),
      type,
      message,
      detail,
    }, ...prev].slice(0, 200));
  }, []);

  const clearLogs = useCallback(() => setLogs([]), []);

  return (
    <LogCtx.Provider value={{ logs, addLog, clearLogs }}>
      {children}
    </LogCtx.Provider>
  );
}

export function useLogs() {
  return useContext(LogCtx);
}
