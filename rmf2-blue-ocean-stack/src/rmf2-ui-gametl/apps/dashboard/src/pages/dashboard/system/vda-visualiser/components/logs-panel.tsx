// Live VDA5050 MQTT traffic feed. Two display modes (concise human-readable
// history vs raw prettified JSON) and a checkbox to hide the high-frequency
// /state topic from the concise/pretty list (the server still buffers and
// sends it — this is a display-only filter).
import { useEffect, useMemo, useRef, useState } from 'react';
import {
  Badge,
  Box,
  Button,
  Checkbox,
  HStack,
  Stack,
  Text,
} from '@chakra-ui/react';

import type { LogDisplayMode, SocketStatus, VdaLogEntry } from '../types';

function robotLabel(entry: VdaLogEntry): string {
  return entry.robot_id ?? `${entry.manufacturer}/${entry.serial_number}`;
}

function formatConciseLog(entry: VdaLogEntry): string {
  const robot = robotLabel(entry);
  const payload = entry.payload as Record<string, unknown> | null;

  if (entry.parse_error || payload == null) {
    return `${robot} — unrecognized message on ${entry.topic}`;
  }

  switch (entry.message_type) {
    case 'order': {
      const nodes = payload.nodes;
      const nodeCount = Array.isArray(nodes) ? nodes.length : '?';
      return `${robot} — order dispatched (orderId=${String(payload.orderId ?? '?')}, ${nodeCount} nodes)`;
    }
    case 'instantActions': {
      if (entry.is_cancel_order) {
        return `${robot} — cancel order requested`;
      }
      const actions = payload.actions;
      const types = Array.isArray(actions)
        ? actions
            .map((a) => (a as Record<string, unknown>)?.actionType)
            .filter(Boolean)
            .join(', ')
        : '?';
      return `${robot} — instant action(s): ${types || '?'}`;
    }
    case 'state': {
      const battery = payload.batteryState as
        | Record<string, unknown>
        | undefined;
      return `${robot} — state update (node=${String(payload.lastNodeId ?? '?')}, battery=${String(battery?.batteryCharge ?? '?')}%)`;
    }
    case 'connection':
      return `${robot} — connection: ${String(payload.connectionState ?? '?')}`;
    case 'visualization': {
      const pos = payload.agvPosition as Record<string, unknown> | undefined;
      const x = typeof pos?.x === 'number' ? pos.x.toFixed(2) : '?';
      const y = typeof pos?.y === 'number' ? pos.y.toFixed(2) : '?';
      return `${robot} — visualization update (x=${x}, y=${y})`;
    }
    case 'factsheet':
      return `${robot} — factsheet received`;
    default:
      return `${robot} — unrecognized message on ${entry.topic}`;
  }
}

function statusPalette(status: SocketStatus) {
  return status === 'connected'
    ? 'green'
    : status === 'connecting'
      ? 'yellow'
      : 'red';
}

// Caps how much text one entry can render — a large /state or factsheet
// payload would otherwise blow up a single card's height.
const MAX_ENTRY_TEXT_LENGTH = 800;

function truncate(text: string): string {
  return text.length > MAX_ENTRY_TEXT_LENGTH
    ? `${text.slice(0, MAX_ENTRY_TEXT_LENGTH)}… (truncated)`
    : text;
}

function LogRow({
  entry,
  mode,
  index,
}: {
  entry: VdaLogEntry;
  mode: LogDisplayMode;
  index: number;
}) {
  return (
    <Box
      bg={index % 2 === 0 ? undefined : 'bg.muted'}
      borderWidth="1px"
      borderColor="border.subtle"
      borderRadius="md"
      px={2}
      py={1.5}
      mb={1.5}
      maxW="100%"
      overflow="hidden"
    >
      <Stack gap={0.5} maxW="100%">
        <HStack gap={2} fontSize="2xs" color="gray.500">
          <Text fontFamily="mono">
            {new Date(entry.ts * 1000).toLocaleTimeString()}
          </Text>
          <Text fontWeight="medium">{robotLabel(entry)}</Text>
          <Badge size="xs" variant="subtle">
            {entry.message_type}
          </Badge>
        </HStack>
        {mode === 'pretty' ? (
          <Text
            as="pre"
            fontSize="xs"
            fontFamily="mono"
            whiteSpace="pre-wrap"
            wordBreak="break-word"
            overflowWrap="anywhere"
          >
            {truncate(
              entry.payload == null
                ? '<unparsed payload>'
                : JSON.stringify(entry.payload, null, 2),
            )}
          </Text>
        ) : (
          <Text fontSize="xs" wordBreak="break-word" overflowWrap="anywhere">
            {truncate(formatConciseLog(entry))}
          </Text>
        )}
      </Stack>
    </Box>
  );
}

export interface LogsPanelProps {
  logs: VdaLogEntry[];
  status: SocketStatus;
}

export function LogsPanel({ logs, status }: LogsPanelProps) {
  const [mode, setMode] = useState<LogDisplayMode>('concise');
  const [suppressState, setSuppressState] = useState(true);
  const [autoScroll, setAutoScroll] = useState(true);
  const scrollRef = useRef<HTMLDivElement>(null);
  const bottomRef = useRef<HTMLDivElement>(null);

  const visible = useMemo(
    () =>
      suppressState ? logs.filter((l) => l.message_type !== 'state') : logs,
    [logs, suppressState],
  );

  useEffect(() => {
    if (autoScroll) bottomRef.current?.scrollIntoView({ block: 'end' });
  }, [visible.length, autoScroll]);

  const onScroll = () => {
    const el = scrollRef.current;
    if (!el) return;
    const atBottom = el.scrollTop + el.clientHeight >= el.scrollHeight - 20;
    setAutoScroll(atBottom);
  };

  return (
    <Stack gap={2} h="100%" minH={0}>
      <HStack justify="space-between" flexWrap="wrap" gap={2} flexShrink={0}>
        <HStack gap={1}>
          <Button
            size="xs"
            variant={mode === 'concise' ? 'solid' : 'outline'}
            onClick={() => setMode('concise')}
          >
            Concise
          </Button>
          <Button
            size="xs"
            variant={mode === 'pretty' ? 'solid' : 'outline'}
            onClick={() => setMode('pretty')}
          >
            Prettify JSON
          </Button>
        </HStack>
        <HStack gap={3}>
          <Checkbox.Root
            size="sm"
            checked={suppressState}
            onCheckedChange={(e) => setSuppressState(!!e.checked)}
          >
            <Checkbox.HiddenInput />
            <Checkbox.Control />
            <Checkbox.Label fontSize="xs">Suppress /state</Checkbox.Label>
          </Checkbox.Root>
          <Badge colorPalette={statusPalette(status)}>{status}</Badge>
        </HStack>
      </HStack>

      <Box
        ref={scrollRef}
        onScroll={onScroll}
        flex="1"
        minH={0}
        overflowY="auto"
        borderWidth="1px"
        borderColor="border.subtle"
        borderRadius="md"
        px={2}
        py={2}
      >
        {visible.length === 0 ? (
          <Text fontSize="sm" color="gray.500" py={4}>
            No MQTT traffic yet.
          </Text>
        ) : (
          visible.map((entry, i) => (
            <LogRow key={entry.seq} entry={entry} mode={mode} index={i} />
          ))
        )}
        <div ref={bottomRef} />
      </Box>
    </Stack>
  );
}

export default LogsPanel;
