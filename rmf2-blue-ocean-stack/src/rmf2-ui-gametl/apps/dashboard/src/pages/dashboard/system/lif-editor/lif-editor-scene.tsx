import '@xyflow/react/dist/style.css';

import { useCallback, useEffect, useRef, useState } from 'react';
import {
  ReactFlow,
  ReactFlowProvider,
  Background,
  BackgroundVariant,
  Controls,
  MiniMap,
  addEdge,
  useNodesState,
  useEdgesState,
  useViewport,
  MarkerType,
  type Node,
  type Edge,
  type Connection,
  type NodeMouseHandler,
  type EdgeMouseHandler,
  type OnNodesDelete,
  type OnEdgesDelete,
} from '@xyflow/react';

import {
  Badge,
  Box,
  Button,
  Center,
  HStack,
  IconButton,
  Select,
  Separator,
  Spinner,
  Stack,
  Text,
  createListCollection,
} from '@chakra-ui/react';
import {
  LuMousePointer2,
  LuCirclePlus,
  LuUndo2,
  LuRedo2,
  LuSave,
  LuFolderOpen,
  LuDownload,
  LuTrash2,
  LuImage,
  LuGrid3X3,
  LuCopy,
  LuGitBranch,
} from 'react-icons/lu';

import {
  notifyTask,
  TaskToaster,
} from '../vda-visualiser/components/task-toaster';
import { Tooltip } from '@/components/ui/tooltip';
import { LifFlowNode } from './components/lif-flow-node';
import { LifEditPanel } from './components/lif-edit-panel';
import { LifMapCanvas } from './components/lif-map-canvas';
import {
  fetchLif,
  fetchRobots,
  fetchVdaMap,
  postLif,
  postNewMap,
  saveRobots,
} from './lif-api';
import { useMapImage } from './use-map-image';
import type {
  LifDocument,
  LifEdge,
  LifLayout,
  LifNode,
  RobotsPayload,
} from './types';
import type { LifMapTool } from './components/lif-map-canvas';
import type { MapData } from '../vda-visualiser/types';

// ---- Constants ----

const SCALE = 30; // 1 LIF meter → 30 px
const MAX_HISTORY = 50;

// ---- Coordinate helpers ----

const toRfX = (x: number) => x * SCALE;
const toRfY = (y: number) => -(y * SCALE);

// ---- Converters ----

function lifNodeToRf(node: LifNode): Node {
  return {
    id: node.nodeId,
    position: { x: toRfX(node.nodePosition.x), y: toRfY(node.nodePosition.y) },
    data: { label: node.nodeName || node.nodeId, lifNode: node },
    type: 'lifNode',
  };
}

function lifEdgeToRf(edge: LifEdge): Edge {
  return {
    id: edge.edgeId,
    source: edge.startNodeId,
    target: edge.endNodeId,
    label: edge.edgeName || edge.edgeId,
    data: { lifEdge: edge },
    type: 'straight',
    markerEnd: {
      type: MarkerType.ArrowClosed,
      color: '#16a085',
      width: 16,
      height: 16,
    },
    style: { strokeWidth: 2, stroke: '#1abc9c' },
  };
}

function patchLayout(
  doc: LifDocument,
  idx: number,
  patch: Partial<LifLayout>,
): LifDocument {
  return {
    ...doc,
    layouts: doc.layouts.map((l, i) => (i === idx ? { ...l, ...patch } : l)),
  };
}

function undirectedEdgeKey(startNodeId: string, endNodeId: string): string {
  return [startNodeId, endNodeId].sort().join('::');
}

function createLifEdge(startNodeId: string, endNodeId: string): LifEdge {
  return {
    edgeId: `${startNodeId}_TO_${endNodeId}`,
    edgeName: `${startNodeId} → ${endNodeId}`,
    startNodeId,
    endNodeId,
  };
}

function vdaMapToLayout(map: MapData, existing?: LifLayout): LifLayout {
  const nodes = Array.isArray(map.nodes) ? map.nodes : [];
  const edges = Array.isArray(map.edges) ? map.edges : [];
  const vdaMapId = map.map_id?.trim() || undefined;
  const defaultMapId =
    existing?.nodes.find((node) => node.mapId.trim() !== '')?.mapId ??
    existing?.layoutId ??
    'vda5050-master';
  const seenEdgeKeys = new Set<string>();
  const uniqueEdges = edges.filter((edge) => {
    const key = undirectedEdgeKey(edge.start_node_id, edge.end_node_id);
    if (seenEdgeKeys.has(key)) return false;
    seenEdgeKeys.add(key);
    return true;
  });

  return {
    layoutId: vdaMapId ?? existing?.layoutId ?? 'AMAV-X',
    layoutName: vdaMapId ?? existing?.layoutName ?? 'AMAV-X',
    layoutVersion: existing?.layoutVersion ?? '1.0.0',
    layoutLevelId: vdaMapId ?? existing?.layoutLevelId ?? 'AMAV-X',
    layoutDescription: 'Cloned from the VDA5050 master /map endpoint.',
    nodes: nodes.map((node) => ({
      nodeId: node.node_id,
      nodeName: node.node_id,
      mapId:
        vdaMapId ??
        existing?.nodes.find(
          (existingNode) => existingNode.nodeId === node.node_id,
        )?.mapId ??
        defaultMapId,
      nodePosition: { x: node.x, y: node.y },
    })),
    edges: uniqueEdges.map((edge) => ({
      edgeId: edge.edge_id,
      edgeName: edge.edge_id,
      startNodeId: edge.start_node_id,
      endNodeId: edge.end_node_id,
    })),
    stations: existing?.stations,
  };
}

function vdaMapToDocument(
  map: MapData,
  current: LifDocument | null,
  layoutIdx: number,
): LifDocument {
  const existing = current?.layouts[layoutIdx];
  const clonedLayout = vdaMapToLayout(map, existing);

  if (current && current.layouts.length > 0) {
    return patchLayout(current, layoutIdx, clonedLayout);
  }

  return {
    metaInformation: {
      projectIdentification: 'VDA5050 Master Map',
      creator: 'RMF2 Dashboard',
      exportTimestamp: new Date().toISOString(),
      lifVersion: '1.0.0',
    },
    layouts: [clonedLayout],
  };
}

const NODE_COORD_DECIMALS = 2;

function roundCoordinate(value: number): number {
  return Number(value.toFixed(NODE_COORD_DECIMALS));
}

function roundNodePosition(position: { x: number; y: number }) {
  return {
    x: roundCoordinate(position.x),
    y: roundCoordinate(position.y),
  };
}

function clamp(value: number, min: number, max: number): number {
  return Math.min(Math.max(value, min), max);
}

function clampPositionToLayout(
  position: { x: number; y: number },
  nodes: LifNode[],
) {
  if (nodes.length < 2) return roundNodePosition(position);
  const xs = nodes.map((node) => node.nodePosition.x);
  const ys = nodes.map((node) => node.nodePosition.y);
  return roundNodePosition({
    x: clamp(position.x, Math.min(...xs), Math.max(...xs)),
    y: clamp(position.y, Math.min(...ys), Math.max(...ys)),
  });
}

type Axis = 'x' | 'y';

export type AdjacentNodeCreateOption = {
  nodeId: string;
  nodeName: string;
  label: string;
  adjustableAxis: Axis;
  axisDirection: -1 | 1;
  defaultPosition: { x: number; y: number };
};

export type AdjacentNodePreview = {
  nodeId: string;
  nodePosition: { x: number; y: number };
};

function parseCoordinateNodeId(value: string) {
  const match = value.trim().match(/^(-?\d+(?:\.\d+)?),(-?\d+(?:\.\d+)?)$/);
  if (!match) return null;
  return { x: Number(match[1]), y: Number(match[2]) };
}

function formatCoordinateNodePart(value: number): string {
  return Number.isInteger(value)
    ? String(value)
    : String(roundCoordinate(value));
}

function average(values: number[]): number | null {
  if (values.length === 0) return null;
  return values.reduce((sum, value) => sum + value, 0) / values.length;
}

function inferAxisStep(
  anchorNode: LifNode,
  anchorGrid: { x: number; y: number },
  nodes: LifNode[],
  axis: Axis,
): number {
  const otherAxis = axis === 'x' ? 'y' : 'x';
  const candidates = nodes.flatMap((node) => {
    const grid = parseCoordinateNodeId(node.nodeId);
    if (!grid || grid[otherAxis] !== anchorGrid[otherAxis]) return [];
    const gridDelta = grid[axis] - anchorGrid[axis];
    if (gridDelta === 0) return [];
    const positionDelta =
      node.nodePosition[axis] - anchorNode.nodePosition[axis];
    const step = positionDelta / gridDelta;
    return Number.isFinite(step) && step !== 0 ? [step] : [];
  });
  const inferred = average(candidates);
  return inferred ?? 1;
}

function inferAdjacentPosition(
  anchorNode: LifNode,
  targetGrid: { x: number; y: number },
  nodes: LifNode[],
) {
  const anchorGrid = parseCoordinateNodeId(anchorNode.nodeId);
  if (!anchorGrid) return roundNodePosition(anchorNode.nodePosition);

  const xFromColumn = average(
    nodes.flatMap((node) => {
      const grid = parseCoordinateNodeId(node.nodeId);
      return grid?.x === targetGrid.x ? [node.nodePosition.x] : [];
    }),
  );
  const yFromRow = average(
    nodes.flatMap((node) => {
      const grid = parseCoordinateNodeId(node.nodeId);
      return grid?.y === targetGrid.y ? [node.nodePosition.y] : [];
    }),
  );

  const x =
    xFromColumn ??
    anchorNode.nodePosition.x +
      (targetGrid.x - anchorGrid.x) *
        inferAxisStep(anchorNode, anchorGrid, nodes, 'x');
  const y =
    yFromRow ??
    anchorNode.nodePosition.y +
      (targetGrid.y - anchorGrid.y) *
        inferAxisStep(anchorNode, anchorGrid, nodes, 'y');

  return roundNodePosition({ x, y });
}

function getAdjacentNodeCreateOptions(
  anchorNode: LifNode | null,
  nodes: LifNode[],
): AdjacentNodeCreateOption[] {
  const anchorGrid = anchorNode
    ? parseCoordinateNodeId(anchorNode.nodeId)
    : null;
  if (!anchorNode || !anchorGrid) return [];

  const usedValues = new Set(
    nodes.flatMap((node) => [node.nodeId, node.nodeName || node.nodeId]),
  );
  const candidates = [
    {
      dx: -1,
      dy: 0,
      label: 'Left',
      adjustableAxis: 'x' as const,
      axisDirection: -1 as const,
    },
    {
      dx: 0,
      dy: -1,
      label: 'Down',
      adjustableAxis: 'y' as const,
      axisDirection: -1 as const,
    },
    {
      dx: 0,
      dy: 1,
      label: 'Up',
      adjustableAxis: 'y' as const,
      axisDirection: 1 as const,
    },
    {
      dx: 1,
      dy: 0,
      label: 'Right',
      adjustableAxis: 'x' as const,
      axisDirection: 1 as const,
    },
  ];

  return candidates.flatMap((candidate) => {
    const targetGrid = {
      x: anchorGrid.x + candidate.dx,
      y: anchorGrid.y + candidate.dy,
    };
    const nodeId = `${formatCoordinateNodePart(
      targetGrid.x,
    )},${formatCoordinateNodePart(targetGrid.y)}`;
    if (usedValues.has(nodeId)) return [];
    const defaultPosition = inferAdjacentPosition(
      anchorNode,
      targetGrid,
      nodes,
    );
    const axis = candidate.adjustableAxis;
    const fixedAxis = axis === 'x' ? 'y' : 'x';
    defaultPosition[fixedAxis] = roundCoordinate(
      anchorNode.nodePosition[fixedAxis],
    );
    const anchorCoordinate = anchorNode.nodePosition[axis];
    const defaultCoordinate = defaultPosition[axis];
    const directionIsValid =
      candidate.axisDirection > 0
        ? defaultCoordinate > anchorCoordinate
        : defaultCoordinate < anchorCoordinate;
    if (!directionIsValid) {
      const step = Math.max(
        Math.abs(inferAxisStep(anchorNode, anchorGrid, nodes, axis)),
        0.01,
      );
      defaultPosition[axis] = roundCoordinate(
        anchorCoordinate + candidate.axisDirection * step,
      );
    }
    return [
      {
        nodeId,
        nodeName: nodeId,
        label: `${nodeId} (${candidate.label})`,
        adjustableAxis: candidate.adjustableAxis,
        axisDirection: candidate.axisDirection,
        defaultPosition,
      },
    ];
  });
}

// ---- Image overlay type ----

export type ImageOverlay = {
  url: string;
  opacity: number;
  naturalW: number;
  naturalH: number;
  scale: number; // RF pixels per image pixel
  x: number; // RF x of image top-left
  y: number; // RF y of image top-left
};

// ---- Image overlay layer (rendered inside ReactFlowProvider — uses useViewport) ----

function ImageOverlayLayer({ overlay }: { overlay: ImageOverlay }) {
  const { x: tx, y: ty, zoom } = useViewport();
  // Compute exact screen position and size — avoids transform/origin quirks.
  // RF world → screen: screenX = rfX * zoom + tx
  const W = overlay.naturalW * overlay.scale;
  const H = overlay.naturalH * overlay.scale;
  return (
    <div
      style={{
        position: 'absolute',
        inset: 0,
        overflow: 'hidden',
        pointerEvents: 'none',
        zIndex: 1,
      }}
    >
      <img
        src={overlay.url}
        draggable={false}
        style={{
          position: 'absolute',
          left: tx + overlay.x * zoom,
          top: ty + overlay.y * zoom,
          width: W * zoom,
          height: H * zoom,
          opacity: overlay.opacity,
        }}
      />
    </div>
  );
}

// ---- Node types (defined at module level — required by React Flow) ----

const NODE_TYPES = { lifNode: LifFlowNode };

type ToolMode = LifMapTool;

// ---- Inner editor ----

function LifEditorInner() {
  const fileInputRef = useRef<HTMLInputElement>(null);
  const imgInputRef = useRef<HTMLInputElement>(null);

  // Data state
  const [lifDoc, setLifDoc] = useState<LifDocument | null>(null);
  const [layoutIdx, setLayoutIdx] = useState(0);
  const [robotsPayload, setRobotsPayload] = useState<RobotsPayload | null>(
    null,
  );

  // Loading / saving
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [submittingMap, setSubmittingMap] = useState(false);
  const [cloningVdaMap, setCloningVdaMap] = useState(false);

  // History (JSON snapshots)
  const snapshots = useRef<string[]>([]);
  const snapIdx = useRef(-1);
  const [canUndo, setCanUndo] = useState(false);
  const [canRedo, setCanRedo] = useState(false);

  // View mode: ReactFlow editor or SVG map canvas
  const [viewMode, setViewMode] = useState<'editor' | 'map'>('map');
  const [mapRotated, setMapRotated] = useState(false);

  // Tool + selection
  const [tool, setTool] = useState<ToolMode>('select');
  const [showGrid, setShowGrid] = useState(true);
  const [selectedNodeId, setSelectedNodeId] = useState<string | null>(null);
  const [selectedEdgeId, setSelectedEdgeId] = useState<string | null>(null);
  const [edgeStartNodeId, setEdgeStartNodeId] = useState<string | null>(null);
  const [adjacentNodePreview, setAdjacentNodePreview] =
    useState<AdjacentNodePreview | null>(null);

  // Background image overlay
  const [imageOverlay, setImageOverlay] = useState<ImageOverlay | null>(null);
  const prevOverlayUrl = useRef<string | null>(null);
  useEffect(() => {
    // Only revoke blob URLs (created from file imports), not server URLs.
    const prev = prevOverlayUrl.current;
    if (prev && prev !== imageOverlay?.url && prev.startsWith('blob:')) {
      URL.revokeObjectURL(prev);
    }
    prevOverlayUrl.current = imageOverlay?.url ?? null;
  }, [imageOverlay?.url]);

  // Auto-load AMAV-X map image from the map server on mount (once only).
  const serverMapImage = useMapImage();
  const mapImageApplied = useRef(false);
  useEffect(() => {
    if (!serverMapImage || mapImageApplied.current) return;
    mapImageApplied.current = true;
    const { url, resolution, origin, width, height } = serverMapImage;
    const scale = resolution * SCALE; // RF pixels per image pixel
    const x = origin[0] * SCALE;
    const y = -(origin[1] + height * resolution) * SCALE;
    setImageOverlay({
      url,
      opacity: 0.45,
      naturalW: width,
      naturalH: height,
      scale,
      x,
      y,
    });
  }, [serverMapImage]);

  // React Flow state
  const [rfNodes, setRfNodes, onRfNodesChange] = useNodesState<Node>([]);
  const [rfEdges, setRfEdges, onRfEdgesChange] = useEdgesState<Edge>([]);

  // Derived
  const layout = lifDoc?.layouts[layoutIdx] ?? null;
  const lifNodes: LifNode[] = layout?.nodes ?? [];
  const lifEdges: LifEdge[] = layout?.edges ?? [];
  const selectedLifNode =
    lifNodes.find((n) => n.nodeId === selectedNodeId) ?? null;
  const selectedLifEdge =
    lifEdges.find((e) => e.edgeId === selectedEdgeId) ?? null;
  const adjacentNodeCreateOptions = getAdjacentNodeCreateOptions(
    selectedLifNode,
    lifNodes,
  );

  useEffect(() => {
    if (tool !== 'createNode' || !selectedLifNode) {
      setAdjacentNodePreview(null);
    }
  }, [selectedLifNode, tool]);

  // ---- History helpers ----

  const takeSnapshot = useCallback((doc: LifDocument) => {
    snapshots.current = snapshots.current.slice(0, snapIdx.current + 1);
    snapshots.current.push(JSON.stringify(doc));
    if (snapshots.current.length > MAX_HISTORY) snapshots.current.shift();
    else snapIdx.current++;
    setCanUndo(snapIdx.current > 0);
    setCanRedo(false);
  }, []);

  const applySnapshot = useCallback(
    (doc: LifDocument) => {
      setLifDoc(doc);
      const l = doc.layouts[layoutIdx];
      if (l) {
        setRfNodes(l.nodes.map(lifNodeToRf));
        setRfEdges(l.edges.map(lifEdgeToRf));
      }
      setSelectedNodeId(null);
      setSelectedEdgeId(null);
      setEdgeStartNodeId(null);
      setAdjacentNodePreview(null);
    },
    [layoutIdx],
  );

  const undo = useCallback(() => {
    if (snapIdx.current <= 0) return;
    snapIdx.current--;
    const doc = JSON.parse(snapshots.current[snapIdx.current]) as LifDocument;
    applySnapshot(doc);
    setCanUndo(snapIdx.current > 0);
    setCanRedo(true);
  }, [applySnapshot]);

  const redo = useCallback(() => {
    if (snapIdx.current >= snapshots.current.length - 1) return;
    snapIdx.current++;
    const doc = JSON.parse(snapshots.current[snapIdx.current]) as LifDocument;
    applySnapshot(doc);
    setCanUndo(true);
    setCanRedo(snapIdx.current < snapshots.current.length - 1);
  }, [applySnapshot]);

  // ---- Initial data load ----

  const initFromDoc = useCallback((doc: LifDocument) => {
    setLifDoc(doc);
    setLayoutIdx(0);
    const l = doc.layouts[0];
    if (l) {
      setRfNodes(l.nodes.map(lifNodeToRf));
      setRfEdges(l.edges.map(lifEdgeToRf));
    } else {
      setRfNodes([]);
      setRfEdges([]);
    }
    snapshots.current = [JSON.stringify(doc)];
    snapIdx.current = 0;
    setCanUndo(false);
    setCanRedo(false);
    setSelectedNodeId(null);
    setSelectedEdgeId(null);
    setEdgeStartNodeId(null);
    setAdjacentNodePreview(null);
  }, []);

  useEffect(() => {
    void (async () => {
      try {
        const [lif, robots] = await Promise.all([fetchLif(), fetchRobots()]);
        try {
          const vdaMap = await fetchVdaMap();
          initFromDoc(vdaMapToDocument(vdaMap, lif, 0));
        } catch (err) {
          initFromDoc(lif);
          notifyTask('warning', 'Using saved LIF', String(err));
        }
        setRobotsPayload(robots);
      } catch (err) {
        notifyTask('error', 'Load failed', String(err));
      } finally {
        setLoading(false);
      }
    })();
  }, [initFromDoc]);

  // ---- Keyboard shortcuts ----

  useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      if ((e.ctrlKey || e.metaKey) && e.key === 'z' && !e.shiftKey) {
        e.preventDefault();
        undo();
      }
      if (
        (e.ctrlKey || e.metaKey) &&
        (e.key === 'y' || (e.key === 'z' && e.shiftKey))
      ) {
        e.preventDefault();
        redo();
      }
    };
    window.addEventListener('keydown', handler);
    return () => window.removeEventListener('keydown', handler);
  }, [undo, redo]);

  // ---- Layout switching ----

  const switchLayout = useCallback(
    (idx: number) => {
      if (!lifDoc) return;
      setLayoutIdx(idx);
      const l = lifDoc.layouts[idx];
      if (l) {
        setRfNodes(l.nodes.map(lifNodeToRf));
        setRfEdges(l.edges.map(lifEdgeToRf));
      }
      setSelectedNodeId(null);
      setSelectedEdgeId(null);
      setEdgeStartNodeId(null);
      setAdjacentNodePreview(null);
    },
    [lifDoc],
  );

  // ---- RF event handlers ----

  const onNodeClick: NodeMouseHandler = useCallback((_e, node) => {
    setSelectedNodeId(node.id);
    setSelectedEdgeId(null);
  }, []);

  const onEdgeClick: EdgeMouseHandler = useCallback((_e, edge) => {
    setSelectedEdgeId(edge.id);
    setSelectedNodeId(null);
  }, []);

  const onPaneClick = useCallback(() => {
    setSelectedNodeId(null);
    setSelectedEdgeId(null);
    setAdjacentNodePreview(null);
  }, []);

  const onConnect = useCallback(
    (connection: Connection) => {
      if (!connection.source || !connection.target) return;
      const source = connection.source;
      const target = connection.target;
      const edgeId = `${source}_TO_${target}`;
      const newRfEdge = lifEdgeToRf({
        edgeId,
        edgeName: `${source} → ${target}`,
        startNodeId: source,
        endNodeId: target,
      });
      setLifDoc((prev) => {
        if (!prev) return prev;
        const l = prev.layouts[layoutIdx];
        const newKey = undirectedEdgeKey(source, target);
        const exists = l.edges.some(
          (edge) =>
            undirectedEdgeKey(edge.startNodeId, edge.endNodeId) === newKey,
        );
        if (exists) return prev;
        const newLifEdge: LifEdge = {
          edgeId,
          edgeName: `${source} → ${target}`,
          startNodeId: source,
          endNodeId: target,
        };
        const next = patchLayout(prev, layoutIdx, {
          edges: [...l.edges, newLifEdge],
        });
        setRfEdges((rfPrev) => addEdge(newRfEdge, rfPrev));
        takeSnapshot(next);
        return next;
      });
    },
    [layoutIdx, takeSnapshot],
  );

  const onNodesDelete: OnNodesDelete = useCallback(
    (nodes) => {
      const ids = new Set(nodes.map((n) => n.id));
      setLifDoc((prev) => {
        if (!prev) return prev;
        const l = prev.layouts[layoutIdx];
        const next = patchLayout(prev, layoutIdx, {
          nodes: l.nodes.filter((n) => !ids.has(n.nodeId)),
          edges: l.edges.filter(
            (e) => !ids.has(e.startNodeId) && !ids.has(e.endNodeId),
          ),
        });
        takeSnapshot(next);
        return next;
      });
      setSelectedNodeId(null);
    },
    [layoutIdx, takeSnapshot],
  );

  const onEdgesDelete: OnEdgesDelete = useCallback(
    (edges) => {
      const ids = new Set(edges.map((e) => e.id));
      setLifDoc((prev) => {
        if (!prev) return prev;
        const l = prev.layouts[layoutIdx];
        const next = patchLayout(prev, layoutIdx, {
          edges: l.edges.filter((e) => !ids.has(e.edgeId)),
        });
        takeSnapshot(next);
        return next;
      });
      setSelectedEdgeId(null);
    },
    [layoutIdx, takeSnapshot],
  );

  // ---- VDA-style map canvas mutations ----

  const commitLayout = useCallback(
    (next: LifDocument, nextLayoutIdx = layoutIdx) => {
      const nextLayout = next.layouts[nextLayoutIdx];
      setLifDoc(next);
      if (nextLayout) {
        setRfNodes(nextLayout.nodes.map(lifNodeToRf));
        setRfEdges(nextLayout.edges.map(lifEdgeToRf));
      }
      takeSnapshot(next);
    },
    [layoutIdx, setRfEdges, setRfNodes, takeSnapshot],
  );

  const handleMapPickNode = useCallback(
    (nodeId: string) => {
      setSelectedNodeId(nodeId);
      setSelectedEdgeId(null);
      if (tool === 'createEdge') {
        setEdgeStartNodeId((prev) => (prev === nodeId ? null : nodeId));
      }
    },
    [tool],
  );

  const handleMapPickEdge = useCallback((edgeId: string) => {
    setSelectedEdgeId(edgeId);
    setSelectedNodeId(null);
    setEdgeStartNodeId(null);
    setAdjacentNodePreview(null);
  }, []);

  const handleCreateNodeToolClick = useCallback(() => {
    if (!selectedLifNode) {
      setTool('select');
      notifyTask(
        'info',
        'Select a node first',
        'Choose the node you want to create from, then press create node.',
      );
      return;
    }
    setTool('createNode');
    setSelectedEdgeId(null);
    setEdgeStartNodeId(null);
    setAdjacentNodePreview(null);
  }, [selectedLifNode]);

  const handleAdjacentNodeCreate = useCallback(
    (option: AdjacentNodeCreateOption, adjustedCoordinate: number) => {
      if (!lifDoc) return;
      const layout = lifDoc.layouts[layoutIdx];
      if (!layout || !selectedLifNode) return;
      if (!Number.isFinite(adjustedCoordinate)) {
        notifyTask(
          'error',
          'Invalid coordinate',
          `${option.adjustableAxis.toUpperCase()} must be a valid number.`,
        );
        return;
      }
      const currentOptions = getAdjacentNodeCreateOptions(
        selectedLifNode,
        layout.nodes,
      );
      const currentOption = currentOptions.find(
        (candidate) => candidate.nodeId === option.nodeId,
      );
      if (!currentOption) {
        notifyTask(
          'error',
          'Node already exists',
          `${option.nodeId} is no longer available.`,
        );
        return;
      }
      const anchorCoordinate =
        selectedLifNode.nodePosition[currentOption.adjustableAxis];
      const directionIsValid =
        currentOption.axisDirection > 0
          ? adjustedCoordinate > anchorCoordinate
          : adjustedCoordinate < anchorCoordinate;
      if (!directionIsValid) {
        notifyTask(
          'error',
          'Invalid direction',
          `${currentOption.adjustableAxis.toUpperCase()} must be ${
            currentOption.axisDirection > 0 ? 'greater than' : 'less than'
          } the selected node value.`,
        );
        return;
      }
      const nodePosition = roundNodePosition({
        ...currentOption.defaultPosition,
        [currentOption.adjustableAxis]: adjustedCoordinate,
      });
      const newNode: LifNode = {
        nodeId: currentOption.nodeId,
        nodeName: currentOption.nodeName,
        mapId: selectedLifNode.mapId,
        nodePosition,
      };
      const newEdgeKey = undirectedEdgeKey(
        selectedLifNode.nodeId,
        newNode.nodeId,
      );
      const nextEdges = layout.edges.some(
        (edge) =>
          undirectedEdgeKey(edge.startNodeId, edge.endNodeId) === newEdgeKey,
      )
        ? layout.edges
        : [
            ...layout.edges,
            createLifEdge(selectedLifNode.nodeId, newNode.nodeId),
          ];
      const next = patchLayout(lifDoc, layoutIdx, {
        nodes: [...layout.nodes, newNode],
        edges: nextEdges,
      });
      commitLayout(next);
      setSelectedNodeId(newNode.nodeId);
      setSelectedEdgeId(null);
      setEdgeStartNodeId(null);
      setAdjacentNodePreview(null);
      notifyTask(
        'success',
        'Node created',
        `${newNode.nodeId} added from ${selectedLifNode.nodeId}.`,
      );
    },
    [commitLayout, layoutIdx, lifDoc, selectedLifNode],
  );

  const handleMapCreateEdge = useCallback(
    (startNodeId: string, endNodeId: string) => {
      if (!lifDoc || startNodeId === endNodeId) return;
      const layout = lifDoc.layouts[layoutIdx];
      if (!layout) return;
      const newKey = undirectedEdgeKey(startNodeId, endNodeId);
      const exists = layout.edges.some(
        (edge) =>
          undirectedEdgeKey(edge.startNodeId, edge.endNodeId) === newKey,
      );
      if (exists) {
        setEdgeStartNodeId(null);
        notifyTask(
          'info',
          'Edge already exists',
          `${startNodeId} and ${endNodeId} are already connected.`,
        );
        return;
      }
      const newEdge = createLifEdge(startNodeId, endNodeId);
      const next = patchLayout(lifDoc, layoutIdx, {
        edges: [...layout.edges, newEdge],
      });
      commitLayout(next);
      setSelectedEdgeId(newEdge.edgeId);
      setSelectedNodeId(null);
      setEdgeStartNodeId(null);
    },
    [commitLayout, layoutIdx, lifDoc],
  );

  // ---- Sidebar mutations ----

  const handleNodeSave = useCallback(
    (updated: LifNode, originalNodeId: string) => {
      if (!lifDoc) return;
      const layout = lifDoc.layouts[layoutIdx];
      if (!layout) return;

      const nextNodeId = updated.nodeId.trim();
      if (!nextNodeId) {
        notifyTask('error', 'Node ID required');
        return;
      }

      const duplicate = layout.nodes.some(
        (node) => node.nodeId === nextNodeId && node.nodeId !== originalNodeId,
      );
      if (duplicate) {
        notifyTask(
          'error',
          'Node ID already exists',
          `${nextNodeId} is already used by another node.`,
        );
        return;
      }

      const nextNodeName = updated.nodeName.trim() || nextNodeId;
      const duplicateName = layout.nodes.some(
        (node) =>
          (node.nodeName || node.nodeId) === nextNodeName &&
          node.nodeId !== originalNodeId,
      );
      if (duplicateName) {
        notifyTask(
          'error',
          'Node name already exists',
          `${nextNodeName} is already used by another node.`,
        );
        return;
      }

      if (
        !Number.isFinite(updated.nodePosition.x) ||
        !Number.isFinite(updated.nodePosition.y)
      ) {
        notifyTask(
          'error',
          'Invalid node position',
          'X and Y must be valid numbers.',
        );
        return;
      }

      const safePosition = clampPositionToLayout(
        updated.nodePosition,
        layout.nodes.filter((node) => node.nodeId !== originalNodeId),
      );
      const positionAdjusted =
        safePosition.x !== roundCoordinate(updated.nodePosition.x) ||
        safePosition.y !== roundCoordinate(updated.nodePosition.y);

      const savedNode: LifNode = {
        ...updated,
        nodeId: nextNodeId,
        nodeName: nextNodeName,
        nodePosition: safePosition,
      };

      const nextEdges = layout.edges.map((edge) => {
        const startNodeId =
          edge.startNodeId === originalNodeId ? nextNodeId : edge.startNodeId;
        const endNodeId =
          edge.endNodeId === originalNodeId ? nextNodeId : edge.endNodeId;
        const oldAutoEdgeId = `${edge.startNodeId}_TO_${edge.endNodeId}`;
        const newAutoEdgeId = `${startNodeId}_TO_${endNodeId}`;
        const oldAutoEdgeName = `${edge.startNodeId} → ${edge.endNodeId}`;
        const newAutoEdgeName = `${startNodeId} → ${endNodeId}`;

        return {
          ...edge,
          startNodeId,
          endNodeId,
          edgeId: edge.edgeId === oldAutoEdgeId ? newAutoEdgeId : edge.edgeId,
          edgeName:
            edge.edgeName === oldAutoEdgeName || edge.edgeName === oldAutoEdgeId
              ? newAutoEdgeName
              : edge.edgeName,
        };
      });

      const next = patchLayout(lifDoc, layoutIdx, {
        nodes: layout.nodes.map((node) =>
          node.nodeId === originalNodeId ? savedNode : node,
        ),
        edges: nextEdges,
      });
      commitLayout(next);
      setSelectedNodeId(nextNodeId);
      setSelectedEdgeId(null);
      setEdgeStartNodeId((current) =>
        current === originalNodeId ? nextNodeId : current,
      );
      notifyTask(
        'success',
        'Node saved',
        (originalNodeId === nextNodeId
          ? `${savedNode.nodeName} updated.`
          : `${originalNodeId} renamed to ${nextNodeId}.`) +
          (positionAdjusted ? ' Position was adjusted to fit map bounds.' : ''),
      );
    },
    [commitLayout, layoutIdx, lifDoc],
  );

  const handleEdgeUpdate = useCallback(
    (updated: LifEdge) => {
      setRfEdges((prev) =>
        prev.map((e) =>
          e.id === updated.edgeId
            ? {
                ...e,
                label: updated.edgeName || updated.edgeId,
                data: { ...e.data, lifEdge: updated },
              }
            : e,
        ),
      );
      setLifDoc((prev) => {
        if (!prev) return prev;
        const l = prev.layouts[layoutIdx];
        const next = patchLayout(prev, layoutIdx, {
          edges: l.edges.map((e) =>
            e.edgeId === updated.edgeId ? updated : e,
          ),
        });
        takeSnapshot(next);
        return next;
      });
    },
    [layoutIdx, takeSnapshot],
  );

  // ---- Delete selected (from sidebar button) ----

  const deleteSelectedNode = useCallback(() => {
    if (!selectedLifNode) return;
    const id = selectedLifNode.nodeId;
    setRfNodes((prev) => prev.filter((n) => n.id !== id));
    setRfEdges((prev) =>
      prev.filter((e) => e.source !== id && e.target !== id),
    );
    setLifDoc((prev) => {
      if (!prev) return prev;
      const l = prev.layouts[layoutIdx];
      const next = patchLayout(prev, layoutIdx, {
        nodes: l.nodes.filter((n) => n.nodeId !== id),
        edges: l.edges.filter(
          (e) => e.startNodeId !== id && e.endNodeId !== id,
        ),
      });
      takeSnapshot(next);
      return next;
    });
    setSelectedNodeId(null);
    setEdgeStartNodeId(null);
  }, [selectedLifNode, layoutIdx, takeSnapshot]);

  const deleteSelectedEdge = useCallback(() => {
    if (!selectedLifEdge) return;
    const id = selectedLifEdge.edgeId;
    setRfEdges((prev) => prev.filter((e) => e.id !== id));
    setLifDoc((prev) => {
      if (!prev) return prev;
      const l = prev.layouts[layoutIdx];
      const next = patchLayout(prev, layoutIdx, {
        edges: l.edges.filter((e) => e.edgeId !== id),
      });
      takeSnapshot(next);
      return next;
    });
    setSelectedEdgeId(null);
    setEdgeStartNodeId(null);
  }, [selectedLifEdge, layoutIdx, takeSnapshot]);

  // ---- File I/O ----

  const handleImport = useCallback(
    (e: React.ChangeEvent<HTMLInputElement>) => {
      const file = e.target.files?.[0];
      if (!file) return;
      const reader = new FileReader();
      reader.onload = (ev) => {
        try {
          const doc = JSON.parse(ev.target?.result as string) as LifDocument;
          initFromDoc(doc);
          notifyTask('success', 'LIF file imported');
        } catch {
          notifyTask('error', 'Invalid LIF file');
        }
      };
      reader.readAsText(file);
      e.target.value = '';
    },
    [initFromDoc],
  );

  const handleExport = useCallback(() => {
    if (!lifDoc) return;
    const blob = new Blob([JSON.stringify(lifDoc, null, 2)], {
      type: 'application/json',
    });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = 'layout.lif.json';
    a.click();
    URL.revokeObjectURL(url);
  }, [lifDoc]);

  const handleImportImage = useCallback(
    (e: React.ChangeEvent<HTMLInputElement>) => {
      const file = e.target.files?.[0];
      if (!file) return;
      const url = URL.createObjectURL(file);
      const img = new Image();
      img.onload = () => {
        // Auto-fit image to current node bounding box
        const xs = rfNodes.map((n) => n.position.x);
        const ys = rfNodes.map((n) => n.position.y);
        const minX = xs.length ? Math.min(...xs) - 60 : 0;
        const minY = ys.length ? Math.min(...ys) - 60 : 0;
        const maxX = xs.length ? Math.max(...xs) + 60 : 400;
        const maxY = ys.length ? Math.max(...ys) + 60 : 400;
        const bboxW = maxX - minX;
        const bboxH = maxY - minY;
        const scale = Math.min(
          bboxW / img.naturalWidth,
          bboxH / img.naturalHeight,
        );
        setImageOverlay({
          url,
          opacity: 0.5,
          naturalW: img.naturalWidth,
          naturalH: img.naturalHeight,
          scale,
          x: minX,
          y: minY,
        });
      };
      img.src = url;
      e.target.value = '';
    },
    [rfNodes],
  );

  const handleCloneVdaMap = useCallback(async () => {
    setCloningVdaMap(true);
    try {
      const vdaMap = await fetchVdaMap();
      const nextDoc = vdaMapToDocument(vdaMap, lifDoc, layoutIdx);
      const nextLayoutIdx = Math.min(layoutIdx, nextDoc.layouts.length - 1);
      const nextLayout = nextDoc.layouts[nextLayoutIdx];
      setLifDoc(nextDoc);
      setLayoutIdx(nextLayoutIdx);
      setRfNodes(nextLayout.nodes.map(lifNodeToRf));
      setRfEdges(nextLayout.edges.map(lifEdgeToRf));
      setSelectedNodeId(null);
      setSelectedEdgeId(null);
      takeSnapshot(nextDoc);
      setViewMode('map');
      notifyTask(
        'success',
        'VDA map cloned',
        `${vdaMap.nodes.length} nodes and ${vdaMap.edges.length} edges copied into the LIF layout.`,
      );
    } catch (err) {
      notifyTask('error', 'Clone failed', String(err));
    } finally {
      setCloningVdaMap(false);
    }
  }, [layoutIdx, lifDoc, setRfEdges, setRfNodes, takeSnapshot]);

  const handleSave = async () => {
    if (!lifDoc) return;
    setSaving(true);
    try {
      const tasks: Promise<unknown>[] = [postLif(lifDoc)];
      if (robotsPayload) tasks.push(saveRobots(robotsPayload));
      await Promise.all(tasks);
      notifyTask('success', 'Saved to server');
    } catch (err) {
      notifyTask('error', 'Save failed', String(err));
    } finally {
      setSaving(false);
    }
  };

  const handleSubmitMap = async () => {
    if (!lifDoc) return;
    setSubmittingMap(true);
    try {
      const result = await postNewMap(lifDoc);
      notifyTask('success', 'Map submitted', result.message);
    } catch (err) {
      notifyTask('error', 'Submit failed', String(err));
    } finally {
      setSubmittingMap(false);
    }
  };

  // ---- Layout select collection ----

  const layoutCollection = createListCollection({
    items: (lifDoc?.layouts ?? []).map((l, i) => ({
      label: l.layoutName || l.layoutId,
      value: String(i),
    })),
  });

  // ---- Render ----

  return (
    <Box display="flex" flexDir="column" h="full" overflow="hidden">
      <TaskToaster />

      {/* Hidden file inputs */}
      <input
        ref={fileInputRef}
        type="file"
        accept=".json"
        style={{ display: 'none' }}
        onChange={handleImport}
      />
      <input
        ref={imgInputRef}
        type="file"
        accept="image/*"
        style={{ display: 'none' }}
        onChange={handleImportImage}
      />

      {/* ── Top bar ── */}
      <HStack
        px={3}
        py={2}
        borderBottomWidth="1px"
        borderColor="border.subtle"
        bg="bg.subtle"
        gap={2}
        flexShrink={0}
        flexWrap="wrap"
      >
        <Text fontWeight="semibold" fontSize="sm" flexShrink={0}>
          VDA Map Editor
        </Text>

        {(lifDoc?.layouts.length ?? 0) > 1 && (
          <Select.Root
            collection={layoutCollection}
            value={[String(layoutIdx)]}
            onValueChange={({ value }) => switchLayout(Number(value[0]))}
            size="sm"
            w="180px"
          >
            <Select.Trigger>
              <Select.ValueText />
            </Select.Trigger>
            <Select.Content>
              {layoutCollection.items.map((item) => (
                <Select.Item key={item.value} item={item}>
                  {item.label}
                </Select.Item>
              ))}
            </Select.Content>
          </Select.Root>
        )}

        {layout && (
          <Badge variant="outline" size="sm">
            {layout.layoutName || layout.layoutId}
          </Badge>
        )}

        <Box flex={1} />

        <Button
          size="sm"
          variant="outline"
          onClick={() => fileInputRef.current?.click()}
        >
          <LuFolderOpen />
          Import LIF
        </Button>

        <Button
          size="sm"
          variant="outline"
          colorPalette="teal"
          loading={cloningVdaMap}
          onClick={() => void handleCloneVdaMap()}
        >
          <LuCopy />
          Clone VDA Map
        </Button>

        <Button
          size="sm"
          variant="outline"
          onClick={() => imgInputRef.current?.click()}
          colorPalette={imageOverlay ? 'teal' : 'gray'}
        >
          <LuImage />
          {imageOverlay ? 'Change image' : 'Import image'}
        </Button>

        <Button
          size="sm"
          variant="outline"
          disabled={!lifDoc}
          onClick={handleExport}
        >
          <LuDownload />
          Export
        </Button>

        <Button
          size="sm"
          variant="outline"
          loading={saving}
          disabled={!lifDoc}
          onClick={() => void handleSave()}
        >
          <LuSave />
          Save draft
        </Button>

        <Button
          size="sm"
          colorPalette="blue"
          loading={submittingMap}
          disabled={!lifDoc}
          onClick={() => void handleSubmitMap()}
        >
          <LuSave />
          Submit map
        </Button>
      </HStack>

      {/* ── Main area ── */}
      <Box flex={1} display="flex" minH={0} overflow="hidden">
        {/* Left toolbar */}
        {(viewMode === 'map' || viewMode === 'editor') && (
          <Stack
            gap={1}
            p={2}
            borderRightWidth="1px"
            borderColor="border.subtle"
            bg="bg.subtle"
            flexShrink={0}
            align="center"
          >
            <Tooltip content="Select / Pan" showArrow>
              <IconButton
                aria-label="Select"
                size="sm"
                variant={tool === 'select' ? 'solid' : 'ghost'}
                colorPalette={tool === 'select' ? 'blue' : 'gray'}
                onClick={() => setTool('select')}
              >
                <LuMousePointer2 />
              </IconButton>
            </Tooltip>

            <Tooltip content="Create adjacent node" showArrow>
              <IconButton
                aria-label="Create adjacent node"
                size="sm"
                variant={tool === 'createNode' ? 'solid' : 'ghost'}
                colorPalette={tool === 'createNode' ? 'teal' : 'gray'}
                onClick={handleCreateNodeToolClick}
              >
                <LuCirclePlus />
              </IconButton>
            </Tooltip>

            <Tooltip content="Connect edge — click two nodes" showArrow>
              <IconButton
                aria-label="Connect edge"
                size="sm"
                variant={tool === 'createEdge' ? 'solid' : 'ghost'}
                colorPalette={tool === 'createEdge' ? 'purple' : 'gray'}
                onClick={() => {
                  setTool('createEdge');
                  setEdgeStartNodeId(null);
                }}
              >
                <LuGitBranch />
              </IconButton>
            </Tooltip>

            <Tooltip content="Delete selected (Del)" showArrow>
              <IconButton
                aria-label="Delete selected"
                size="sm"
                variant="ghost"
                colorPalette="red"
                disabled={!selectedNodeId && !selectedEdgeId}
                onClick={
                  selectedNodeId ? deleteSelectedNode : deleteSelectedEdge
                }
              >
                <LuTrash2 />
              </IconButton>
            </Tooltip>

            <Tooltip content="Toggle grid" showArrow>
              <IconButton
                aria-label="Toggle grid"
                size="sm"
                variant={showGrid ? 'solid' : 'ghost'}
                colorPalette={showGrid ? 'gray' : 'gray'}
                onClick={() => setShowGrid((v) => !v)}
                disabled={viewMode === 'map'}
              >
                <LuGrid3X3 />
              </IconButton>
            </Tooltip>

            <Separator orientation="horizontal" />

            <Tooltip content="Undo (Ctrl+Z)" showArrow>
              <IconButton
                aria-label="Undo"
                size="sm"
                variant="ghost"
                disabled={!canUndo}
                onClick={undo}
              >
                <LuUndo2 />
              </IconButton>
            </Tooltip>

            <Tooltip content="Redo (Ctrl+Y)" showArrow>
              <IconButton
                aria-label="Redo"
                size="sm"
                variant="ghost"
                disabled={!canRedo}
                onClick={redo}
              >
                <LuRedo2 />
              </IconButton>
            </Tooltip>
          </Stack>
        )}

        {/* Canvas — ReactFlow editor or SVG map view */}
        <Box flex={1} position="relative" bg="white">
          {viewMode === 'editor' ? (
            <>
              {/* Background image overlay — behind RF nodes */}
              {imageOverlay && <ImageOverlayLayer overlay={imageOverlay} />}

              {loading && (
                <Center position="absolute" inset={0} zIndex={10} bg="white/80">
                  <Spinner />
                </Center>
              )}

              <ReactFlow
                nodes={rfNodes}
                edges={rfEdges}
                nodeTypes={NODE_TYPES}
                onNodesChange={onRfNodesChange}
                onEdgesChange={onRfEdgesChange}
                onConnect={onConnect}
                onNodeClick={onNodeClick}
                onEdgeClick={onEdgeClick}
                onPaneClick={onPaneClick}
                nodesDraggable={false}
                onNodesDelete={onNodesDelete}
                onEdgesDelete={onEdgesDelete}
                deleteKeyCode={['Backspace', 'Delete']}
                fitView
                fitViewOptions={{ padding: 0.3 }}
                style={{
                  background: 'transparent',
                  cursor: undefined,
                }}
              >
                {showGrid && (
                  <Background
                    variant={BackgroundVariant.Dots}
                    gap={SCALE}
                    size={1.5}
                    color="#d0d0d0"
                  />
                )}
                <Controls />
                <MiniMap
                  nodeColor={(n) => (n.selected ? '#ff9800' : '#1abc9c')}
                  maskColor="rgba(240,240,240,0.7)"
                  style={{ bottom: 48 }}
                />
              </ReactFlow>
            </>
          ) : (
            <LifMapCanvas
              lifNodes={lifNodes}
              lifEdges={lifEdges}
              selectedNodeId={selectedNodeId}
              selectedEdgeId={selectedEdgeId}
              edgeStartNodeId={edgeStartNodeId}
              previewNode={adjacentNodePreview}
              tool={tool === 'createNode' ? 'select' : tool}
              onPickNode={handleMapPickNode}
              onPickEdge={handleMapPickEdge}
              onCreateNode={() => undefined}
              onCreateEdge={handleMapCreateEdge}
              rotated={mapRotated}
              onToggleRotate={() => setMapRotated((v) => !v)}
              mapImage={serverMapImage}
            />
          )}
        </Box>

        {/* Right edit panel */}
        <Box
          w="300px"
          flexShrink={0}
          borderLeftWidth="1px"
          borderColor="border.subtle"
          overflow="hidden"
          display="flex"
          flexDir="column"
        >
          <LifEditPanel
            selectedNode={selectedLifNode}
            selectedEdge={selectedLifEdge}
            lifNodes={lifNodes}
            adjacentNodeCreateOptions={adjacentNodeCreateOptions}
            showCreateAdjacentNode={tool === 'createNode'}
            onNodeSave={handleNodeSave}
            onCreateAdjacentNode={handleAdjacentNodeCreate}
            onAdjacentNodePreviewChange={setAdjacentNodePreview}
            onEdgeUpdate={handleEdgeUpdate}
            onDeleteNode={deleteSelectedNode}
            onDeleteEdge={deleteSelectedEdge}
            imageOverlay={imageOverlay}
            onImageOverlayChange={setImageOverlay}
            onImportImage={() => imgInputRef.current?.click()}
          />
        </Box>
      </Box>

      {/* ── Footer ── */}
      <HStack
        px={3}
        py={1}
        borderTopWidth="1px"
        borderColor="border.subtle"
        bg="bg.subtle"
        gap={4}
        flexShrink={0}
      >
        <Text fontSize="xs" color="fg.muted">
          Nodes:{' '}
          <Text as="span" fontWeight="semibold">
            {lifNodes.length}
          </Text>
        </Text>
        <Text fontSize="xs" color="fg.muted">
          Edges:{' '}
          <Text as="span" fontWeight="semibold">
            {lifEdges.length}
          </Text>
        </Text>
        {layout && (
          <Text fontSize="xs" color="fg.muted">
            {layout.layoutName || layout.layoutId}
          </Text>
        )}
        <Box flex={1} />
        <Text fontSize="xs" color="fg.muted">
          {tool === 'createEdge'
            ? edgeStartNodeId
              ? `Select target node for ${edgeStartNodeId}`
              : 'Select the first node, then the target node'
            : 'Select a node to create adjacent nodes from the right panel'}
        </Text>
      </HStack>
    </Box>
  );
}

// ---- Exported page component ----

export function LifEditorScene() {
  return (
    <ReactFlowProvider>
      <LifEditorInner />
    </ReactFlowProvider>
  );
}

export default LifEditorScene;
