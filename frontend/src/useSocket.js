import { useCallback, useEffect, useRef, useState } from "react";
import { websocketUrl } from "./api";

export function useSocket(onEvent) {
  const callbackRef = useRef(onEvent);
  const socketRef = useRef(null);
  const [connected, setConnected] = useState(false);

  useEffect(() => {
    callbackRef.current = onEvent;
  }, [onEvent]);

  useEffect(() => {
    let stopped = false;
    let reconnectTimer;
    let pingTimer;

    const connect = () => {
      const socket = new WebSocket(websocketUrl());
      socketRef.current = socket;
      socket.addEventListener("open", () => {
        setConnected(true);
        pingTimer = window.setInterval(() => {
          if (socket.readyState === WebSocket.OPEN) {
            socket.send(JSON.stringify({ type: "ping" }));
          }
        }, 25000);
      });
      socket.addEventListener("message", (event) => {
        try {
          callbackRef.current?.(JSON.parse(event.data));
        } catch {
          // Ignore malformed server events and keep the socket alive.
        }
      });
      socket.addEventListener("close", (event) => {
        setConnected(false);
        window.clearInterval(pingTimer);
        if (event.code === 4401) {
          window.location.assign("/login");
        } else if (!stopped) {
          reconnectTimer = window.setTimeout(connect, 1400);
        }
      });
    };

    connect();
    return () => {
      stopped = true;
      window.clearTimeout(reconnectTimer);
      window.clearInterval(pingTimer);
      socketRef.current?.close();
    };
  }, []);

  const send = useCallback((payload) => {
    if (socketRef.current?.readyState !== WebSocket.OPEN) return false;
    socketRef.current.send(JSON.stringify(payload));
    return true;
  }, []);

  return { connected, send };
}

