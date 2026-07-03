// Live VDA5050 MQTT traffic feed from the master over a WebSocket, with
// retry. Mirrors use-vda-state.ts's connect/retry shape, plus seq-based
// dedup so a reconnect's replayed history doesn't duplicate already-shown
// entries.
import { useEffect, useRef, useState } from 'react';
import { BrokerStatusConfig } from '@/clients';

import type { SocketStatus, VdaLogEntry } from './types';

const WS_URL =
  (BrokerStatusConfig.BASE ?? 'http://localhost:8000').replace(/^http/, 'ws') +
  '/ws/logs';

const MAX_CLIENT_LOGS = 1000;

export function useVdaLogs() {
  const [logs, setLogs] = useState<VdaLogEntry[]>([]);
  const [status, setStatus] = useState<SocketStatus>('connecting');
  const retryRef = useRef<ReturnType<typeof setTimeout>>(undefined);
  const lastSeqRef = useRef(-1);

  useEffect(() => {
    let socket: WebSocket;
    let closed = false;

    const connect = () => {
      setStatus('connecting');
      socket = new WebSocket(WS_URL);
      socket.onopen = () => setStatus('connected');
      socket.onmessage = (event) => {
        const msg = JSON.parse(event.data);
        if (msg.type !== 'log') return;
        const entry = msg as VdaLogEntry;
        if (entry.seq <= lastSeqRef.current) return;
        lastSeqRef.current = entry.seq;
        setLogs((prev) => {
          const next = [...prev, entry];
          return next.length > MAX_CLIENT_LOGS
            ? next.slice(next.length - MAX_CLIENT_LOGS)
            : next;
        });
      };
      socket.onclose = () => {
        setStatus('disconnected');
        if (!closed) {
          retryRef.current = setTimeout(connect, 2000);
        }
      };
    };
    connect();

    return () => {
      closed = true;
      clearTimeout(retryRef.current);
      socket.close();
    };
  }, []);

  return { logs, status };
}
