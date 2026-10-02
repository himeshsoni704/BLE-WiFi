import { useEffect, useRef, useState } from "react";
import { wsUrl } from "../services/api";

export type WsStatus = "connecting" | "open" | "closed";

/** Subscribes to a backend WS feed, reconnecting with backoff on drop.
 * Returns the latest parsed JSON payload and the connection status (shown
 * in the UI as a small "live" indicator -- never silently stale data). */
export function useWebSocket<T>(path: string, enabled = true): { data: T | null; status: WsStatus } {
  const [data, setData] = useState<T | null>(null);
  const [status, setStatus] = useState<WsStatus>("connecting");
  const attempt = useRef(0);

  useEffect(() => {
    if (!enabled) return;
    let ws: WebSocket | null = null;
    let closedByEffect = false;
    let retryTimer: ReturnType<typeof setTimeout> | undefined;

    const connect = () => {
      setStatus("connecting");
      ws = new WebSocket(wsUrl(path));
      ws.onopen = () => {
        attempt.current = 0;
        setStatus("open");
      };
      ws.onmessage = (ev) => {
        try {
          setData(JSON.parse(ev.data));
        } catch {
          // ignore malformed frames
        }
      };
      ws.onclose = () => {
        setStatus("closed");
        if (closedByEffect) return;
        const delay = Math.min(1000 * 2 ** attempt.current, 15000);
        attempt.current += 1;
        retryTimer = setTimeout(connect, delay);
      };
      ws.onerror = () => ws?.close();
    };

    connect();
    return () => {
      closedByEffect = true;
      if (retryTimer) clearTimeout(retryTimer);
      ws?.close();
    };
  }, [path, enabled]);

  return { data, status };
}
