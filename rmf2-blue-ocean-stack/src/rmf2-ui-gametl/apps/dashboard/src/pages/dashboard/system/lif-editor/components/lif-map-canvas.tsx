// SVG render of the LIF topology: map image backdrop, edges, nodes (click =
// select). Mouse wheel zooms, click-drag pans; default view fits all nodes.
// Mirrors the VDA visualiser's MapCanvas for a consistent look and feel.
//
// Coordinate system: world metres (y-up) → SVG pixels (y-down), fixed at
// SCALE px/metre. The SVG origin is anchored to the union of the map image
// bounds and the node positions, so the image and nodes always align.
import { useEffect, useMemo, useRef, useState } from 'react';
import { Box, Button, HStack, IconButton, Text } from '@chakra-ui/react';
import { LuRotateCcw } from 'react-icons/lu';

import { Tooltip } from '@/components/ui/tooltip';
import { ToggleTip } from '@/components/ui/toggle-tip';
import type { MapImageMeta } from '../use-map-image';
import type { LifEdge, LifNode } from '../types';
import {
  axisArrowHead,
  boundsFromPoints,
  computeProjection,
  expandBounds,
  imageTransform,
  mapImageBoundsPx,
  SCALE,
  type BoundsPx,
  type Vec2,
} from '../../vda-visualiser/components/projection';
import {
  FIT_DEMO_VIEW,
  SELECTED_COLOR,
  TEXT_SHADOW,
  VISUAL,
} from '../../vda-visualiser/components/constants';

const MIN_ZOOM = 0.5;
const MAX_ZOOM = 12;
const ZOOM_PRESETS = [50, 75, 100, 125, 150, 175] as const;
const DRAG_THRESHOLD = 4;
const FIT_PADDING = 0.05; // fraction of span added when fitting

const SELECTED_STROKE = '#2b6cb0';
const AXIS_LEN_SVG = 36;
const AXIS_MARGIN = 5;

type FitMode = 'map' | 'nodes' | 'demo';
export type LifMapTool = 'select' | 'createNode' | 'createEdge';

const FIT_MODES: FitMode[] = ['map', 'nodes', 'demo'];
const FIT_MODE_LABEL: Record<FitMode, string> = {
  map: 'Fit Map',
  nodes: 'Fit Nodes',
  demo: 'Fit Demo',
};

export interface LifMapCanvasProps {
  lifNodes: LifNode[];
  lifEdges: LifEdge[];
  selectedNodeId: string | null;
  selectedEdgeId: string | null;
  edgeStartNodeId: string | null;
  previewNode: {
    nodeId: string;
    nodePosition: { x: number; y: number };
  } | null;
  tool: LifMapTool;
  onPickNode: (nodeId: string) => void;
  onPickEdge: (edgeId: string) => void;
  onCreateNode: (position: { x: number; y: number }) => void;
  onCreateEdge: (startNodeId: string, endNodeId: string) => void;
  rotated: boolean;
  onToggleRotate?: () => void;
  mapImage: MapImageMeta | null;
}

export function LifMapCanvas({
  lifNodes,
  lifEdges,
  selectedNodeId,
  selectedEdgeId,
  edgeStartNodeId,
  previewNode,
  tool,
  onPickNode,
  onPickEdge,
  onCreateNode,
  onCreateEdge,
  rotated,
  onToggleRotate,
  mapImage,
}: LifMapCanvasProps) {
  const nodes = useMemo(
    () =>
      lifNodes.map((n) => ({
        node_id: n.nodeId,
        x: n.nodePosition.x,
        y: n.nodePosition.y,
      })),
    [lifNodes],
  );
  const projectionNodes = useMemo(
    () =>
      previewNode
        ? [
            ...nodes,
            {
              node_id: previewNode.nodeId,
              x: previewNode.nodePosition.x,
              y: previewNode.nodePosition.y,
            },
          ]
        : nodes,
    [nodes, previewNode],
  );
  const nodeById = useMemo(
    () => new Map(nodes.map((n) => [n.node_id, n])),
    [nodes],
  );

  const { svgW, svgH, toPx, axis } = useMemo(
    () => computeProjection(projectionNodes, rotated),
    [projectionNodes, rotated],
  );

  const svgRef = useRef<SVGSVGElement>(null);
  const [view, setView] = useState({ x: 0, y: 0, w: svgW, h: svgH });
  const viewRef = useRef(view);
  viewRef.current = view;
  const suppressClickRef = useRef(false);
  const [grabbing, setGrabbing] = useState(false);
  const [edgePreviewPoint, setEdgePreviewPoint] = useState<Vec2 | null>(null);
  const [zoomTipOpen, setZoomTipOpen] = useState(false);
  const zoomTipPinnedRef = useRef(false);
  const [fitModeIndex, setFitModeIndex] = useState<number>(() =>
    FIT_MODES.indexOf('nodes'),
  );

  // Re-fit when projection changes (map loads, rotation toggles).
  useEffect(() => {
    setView({ x: 0, y: 0, w: svgW, h: svgH });
    setFitModeIndex(FIT_MODES.indexOf('nodes'));
  }, [svgW, svgH]);

  useEffect(() => {
    if (tool !== 'createEdge' || !edgeStartNodeId) {
      setEdgePreviewPoint(null);
    }
  }, [edgeStartNodeId, tool]);

  const clampViewWidth = (w: number) =>
    Math.min(Math.max(w, svgW / MAX_ZOOM), svgW / MIN_ZOOM);

  const setZoomAboutCenter = (targetPct: number) => {
    const pct = Math.min(Math.max(targetPct, MIN_ZOOM * 100), MAX_ZOOM * 100);
    const { x, y, w, h } = viewRef.current;
    const cx = x + w / 2;
    const cy = y + h / 2;
    const newW = clampViewWidth(svgW / (pct / 100));
    const newH = h * (newW / w);
    setView({ x: cx - newW / 2, y: cy - newH / 2, w: newW, h: newH });
  };

  const stepZoom = (direction: -1 | 1) => {
    const current = Math.round((svgW / viewRef.current.w) * 100);
    if (direction > 0) {
      const next = ZOOM_PRESETS.find((p) => p > current);
      setZoomAboutCenter(next ?? Math.min(current + 25, MAX_ZOOM * 100));
    } else {
      const next = [...ZOOM_PRESETS].reverse().find((p) => p < current);
      setZoomAboutCenter(next ?? Math.max(current - 25, MIN_ZOOM * 100));
    }
  };

  const openZoomTip = () => setZoomTipOpen(true);
  const closeZoomTip = () => {
    if (!zoomTipPinnedRef.current) setZoomTipOpen(false);
  };
  const dismissZoomTip = () => {
    zoomTipPinnedRef.current = false;
    setZoomTipOpen(false);
  };
  const toggleZoomTipPin = () => {
    if (zoomTipPinnedRef.current && zoomTipOpen) dismissZoomTip();
    else {
      zoomTipPinnedRef.current = true;
      setZoomTipOpen(true);
    }
  };

  const setViewFromBounds = (bounds: BoundsPx) => {
    setView({
      x: bounds.minX,
      y: bounds.minY,
      w: bounds.maxX - bounds.minX,
      h: bounds.maxY - bounds.minY,
    });
  };

  const clientToSvg = (clientX: number, clientY: number): Vec2 | null => {
    const svg = svgRef.current;
    if (!svg) return null;
    const rect = svg.getBoundingClientRect();
    return {
      x:
        viewRef.current.x +
        ((clientX - rect.left) / rect.width) * viewRef.current.w,
      y:
        viewRef.current.y +
        ((clientY - rect.top) / rect.height) * viewRef.current.h,
    };
  };

  const svgDeltaToWorld = (dx: number, dy: number) => ({
    x: (dx * axis.xDir.x + dy * axis.xDir.y) / SCALE,
    y: (dx * axis.yDir.x + dy * axis.yDir.y) / SCALE,
  });

  const svgToWorld = (point: Vec2) => {
    const origin = toPx(0, 0);
    const delta = svgDeltaToWorld(point.x - origin.x, point.y - origin.y);
    return {
      x: Number(delta.x.toFixed(3)),
      y: Number(delta.y.toFixed(3)),
    };
  };

  const fitNodes = () => {
    const pts = nodes.map((n) => toPx(n.x, n.y));
    const b = boundsFromPoints(pts);
    if (!b) {
      setView({ x: 0, y: 0, w: svgW, h: svgH });
      return;
    }
    setViewFromBounds(expandBounds(b, FIT_PADDING));
  };

  const fitMap = () => {
    let cx = svgW / 2;
    let cy = svgH / 2;
    if (mapImage) {
      const mapBounds = mapImageBoundsPx(mapImage, toPx);
      cx = (mapBounds.minX + mapBounds.maxX) / 2;
      cy = (mapBounds.minY + mapBounds.maxY) / 2;
    }
    const newW = clampViewWidth(svgW / MIN_ZOOM);
    const newH = newW * (svgH / svgW);
    setView({ x: cx - newW / 2, y: cy - newH / 2, w: newW, h: newH });
  };

  const fitDemo = () => {
    const center = toPx(FIT_DEMO_VIEW.x, FIT_DEMO_VIEW.y);
    const newW = clampViewWidth(svgW / (FIT_DEMO_VIEW.zoomPct / 100));
    const newH = newW * (svgH / svgW);
    setView({
      x: center.x - newW / 2,
      y: center.y - newH / 2,
      w: newW,
      h: newH,
    });
  };

  const applyFit = (mode: FitMode) => {
    if (mode === 'map') fitMap();
    else if (mode === 'demo') fitDemo();
    else fitNodes();
  };

  const cycleFit = () => {
    const nextIndex = (fitModeIndex + 1) % FIT_MODES.length;
    const mode = FIT_MODES[nextIndex]!;
    applyFit(mode);
    setFitModeIndex(nextIndex);
  };

  // Wheel zoom, non-passive so we can preventDefault.
  useEffect(() => {
    const svg = svgRef.current;
    if (!svg) return;
    const onWheel = (e: WheelEvent) => {
      e.preventDefault();
      const rect = svg.getBoundingClientRect();
      const { x, y, w, h } = viewRef.current;
      const fracX = (e.clientX - rect.left) / rect.width;
      const fracY = (e.clientY - rect.top) / rect.height;
      const vx = x + fracX * w;
      const vy = y + fracY * h;
      const raw = w * (e.deltaY > 0 ? 1.1 : 1 / 1.1);
      const newW = clampViewWidth(raw);
      const newH = h * (newW / w);
      setView({ x: vx - fracX * newW, y: vy - fracY * newH, w: newW, h: newH });
    };
    svg.addEventListener('wheel', onWheel, { passive: false });
    return () => svg.removeEventListener('wheel', onWheel);
  }, [svgW]);

  // Click-drag to pan; tracks distance to distinguish pan from click.
  const onMouseDown = (e: React.MouseEvent) => {
    const svg = svgRef.current;
    if (!svg) return;
    suppressClickRef.current = false;
    const rect = svg.getBoundingClientRect();
    const start = { cx: e.clientX, cy: e.clientY, ...viewRef.current };
    // viewBox units per client pixel. preserveAspectRatio="…meet" scales the
    // viewBox uniformly by min(rect.w/w, rect.h/h), so the true units-per-pixel
    // is max(w/rect.w, h/rect.h). Using this keeps the map locked 1:1 to the
    // cursor even when the element's aspect ratio differs from the viewBox's.
    const k = Math.max(start.w / rect.width, start.h / rect.height);
    setGrabbing(true);
    const onMove = (ev: MouseEvent) => {
      const dx = ev.clientX - start.cx;
      const dy = ev.clientY - start.cy;
      if (Math.hypot(dx, dy) > DRAG_THRESHOLD) suppressClickRef.current = true;
      setView({
        x: start.x - dx * k,
        y: start.y - dy * k,
        w: start.w,
        h: start.h,
      });
    };
    const onUp = () => {
      window.removeEventListener('mousemove', onMove);
      window.removeEventListener('mouseup', onUp);
      setGrabbing(false);
    };
    window.addEventListener('mousemove', onMove);
    window.addEventListener('mouseup', onUp);
  };

  const handlePickNode = (id: string) => {
    if (!suppressClickRef.current) onPickNode(id);
  };

  const onSvgClick = (e: React.MouseEvent<SVGSVGElement>) => {
    if (suppressClickRef.current || e.target !== svgRef.current) return;
    if (tool === 'createNode') {
      const point = clientToSvg(e.clientX, e.clientY);
      if (point) onCreateNode(svgToWorld(point));
    }
  };

  const onSvgMouseMove = (e: React.MouseEvent<SVGSVGElement>) => {
    if (tool !== 'createEdge' || !edgeStartNodeId) return;
    setEdgePreviewPoint(clientToSvg(e.clientX, e.clientY));
  };

  const onNodeMouseDown = (
    e: React.MouseEvent<SVGGElement>,
    nodeId: string,
  ) => {
    e.stopPropagation();
    suppressClickRef.current = false;
    if (tool === 'createEdge') {
      if (edgeStartNodeId && edgeStartNodeId !== nodeId) {
        suppressClickRef.current = true;
        onCreateEdge(edgeStartNodeId, nodeId);
      } else {
        onPickNode(nodeId);
      }
      return;
    }

    onPickNode(nodeId);
  };

  const zoomPct = Math.round((svgW / view.w) * 100);
  const currentFitMode = FIT_MODES[fitModeIndex]!;

  // Axis overlay — fixed SVG in the top-left corner.
  const aO = { x: AXIS_MARGIN + 4, y: AXIS_MARGIN + 4 };
  const xTip = {
    x: aO.x + axis.xDir.x * AXIS_LEN_SVG,
    y: aO.y + axis.xDir.y * AXIS_LEN_SVG,
  };
  const yTip = {
    x: aO.x + axis.yDir.x * AXIS_LEN_SVG,
    y: aO.y + axis.yDir.y * AXIS_LEN_SVG,
  };
  const xLabel = { x: xTip.x + axis.xDir.x * 14, y: xTip.y + axis.xDir.y * 14 };
  const yLabel = { x: yTip.x + axis.yDir.x * 14, y: yTip.y + axis.yDir.y * 14 };
  const axisFont = 12;
  const axisDotR = 4;
  const allAxisPts = [
    aO,
    xTip,
    yTip,
    xLabel,
    yLabel,
    { x: xLabel.x + 10, y: xLabel.y + 10 },
    { x: yLabel.x + 10, y: yLabel.y + 10 },
    { x: aO.x - axisDotR, y: aO.y - axisDotR },
  ];
  let axisMinX = Infinity,
    axisMinY = Infinity,
    axisMaxX = -Infinity,
    axisMaxY = -Infinity;
  for (const p of allAxisPts) {
    axisMinX = Math.min(axisMinX, p.x);
    axisMinY = Math.min(axisMinY, p.y);
    axisMaxX = Math.max(axisMaxX, p.x);
    axisMaxY = Math.max(axisMaxY, p.y);
  }
  const axisVX = axisMinX - AXIS_MARGIN,
    axisVY = axisMinY - AXIS_MARGIN;
  const axisVW = axisMaxX - axisMinX + 2 * AXIS_MARGIN;
  const axisVH = axisMaxY - axisMinY + 2 * AXIS_MARGIN;
  const axisDisplaySize = 120;
  const axisDispW = Math.round(
    axisDisplaySize * (axisVW / Math.max(axisVW, axisVH)),
  );
  const axisDispH = Math.round(
    axisDisplaySize * (axisVH / Math.max(axisVW, axisVH)),
  );

  return (
    <Box
      position="relative"
      borderWidth="1px"
      borderRadius="lg"
      bg="white"
      overflow="hidden"
      maxW="100%"
      h="100%"
      display="flex"
      flexDirection="column"
    >
      {nodes.length === 0 && !mapImage ? (
        <Text color="gray.500" p={4}>
          No nodes in this layout.
        </Text>
      ) : (
        <>
          {/* Axis overlay — top-left */}
          <Box
            position="absolute"
            top={2}
            left={2}
            zIndex={1}
            pointerEvents="none"
          >
            <Box
              bg="white"
              borderRadius="md"
              px={2}
              py={1}
              display="inline-block"
              lineHeight={0}
            >
              <svg
                width={axisDispW}
                height={axisDispH}
                viewBox={`${axisVX} ${axisVY} ${axisVW} ${axisVH}`}
                aria-hidden
              >
                <line
                  x1={aO.x}
                  y1={aO.y}
                  x2={xTip.x}
                  y2={xTip.y}
                  stroke="#e53e3e"
                  strokeWidth={3}
                  strokeLinecap="round"
                />
                <polygon
                  points={axisArrowHead(xTip.x, xTip.y, aO.x, aO.y, 9)}
                  fill="#e53e3e"
                />
                <text
                  x={xLabel.x}
                  y={xLabel.y}
                  fontSize={axisFont}
                  fontWeight={800}
                  fill="#e53e3e"
                  dominantBaseline="middle"
                  textAnchor="middle"
                >
                  +x
                </text>
                <line
                  x1={aO.x}
                  y1={aO.y}
                  x2={yTip.x}
                  y2={yTip.y}
                  stroke="#38a169"
                  strokeWidth={3}
                  strokeLinecap="round"
                />
                <polygon
                  points={axisArrowHead(yTip.x, yTip.y, aO.x, aO.y, 9)}
                  fill="#38a169"
                />
                <text
                  x={yLabel.x}
                  y={yLabel.y}
                  fontSize={axisFont}
                  fontWeight={800}
                  fill="#38a169"
                  dominantBaseline="middle"
                  textAnchor="middle"
                >
                  +y
                </text>
                <circle cx={aO.x} cy={aO.y} r={axisDotR} fill="#718096" />
              </svg>
            </Box>
          </Box>

          {/* Zoom / rotate controls — top-right */}
          <Box
            position="absolute"
            top={2}
            right={2}
            left={2}
            zIndex={1}
            display="flex"
            justifyContent="flex-end"
            pointerEvents="none"
          >
            <HStack
              gap={2}
              bg="white"
              borderRadius="md"
              px={3}
              py={1.5}
              flexShrink={0}
              pointerEvents="auto"
            >
              <Tooltip content="Zoom out" showArrow>
                <IconButton
                  aria-label="Zoom out"
                  size="xs"
                  variant="outline"
                  flexShrink={0}
                  onClick={() => stepZoom(-1)}
                >
                  −
                </IconButton>
              </Tooltip>
              <ToggleTip
                manualTrigger
                open={zoomTipOpen}
                onOpenChange={(e) => {
                  if (!e.open) dismissZoomTip();
                }}
                showArrow
                content={
                  <HStack gap={0.5}>
                    {ZOOM_PRESETS.map((preset) => (
                      <Button
                        key={preset}
                        size="2xs"
                        variant={zoomPct === preset ? 'solid' : 'ghost'}
                        bg={zoomPct === preset ? 'black' : undefined}
                        color={zoomPct === preset ? 'white' : undefined}
                        _hover={
                          zoomPct === preset ? { bg: 'gray.800' } : undefined
                        }
                        onClick={() => {
                          setZoomAboutCenter(preset);
                          dismissZoomTip();
                        }}
                      >
                        {preset}%
                      </Button>
                    ))}
                  </HStack>
                }
                contentProps={{
                  onMouseEnter: openZoomTip,
                  onMouseLeave: closeZoomTip,
                }}
              >
                <Button
                  size="xs"
                  variant="ghost"
                  fontSize="xs"
                  fontWeight={600}
                  color="gray.700"
                  minW="3.5em"
                  px={2}
                  onMouseEnter={openZoomTip}
                  onMouseLeave={closeZoomTip}
                  onClick={toggleZoomTipPin}
                >
                  {zoomPct}%
                </Button>
              </ToggleTip>
              <Tooltip content="Zoom in" showArrow>
                <IconButton
                  aria-label="Zoom in"
                  size="xs"
                  variant="outline"
                  flexShrink={0}
                  onClick={() => stepZoom(1)}
                >
                  +
                </IconButton>
              </Tooltip>
              {onToggleRotate && (
                <Tooltip content="Rotate map 90°" showArrow>
                  <IconButton
                    aria-label="Rotate map 90°"
                    size="xs"
                    variant={rotated ? 'solid' : 'outline'}
                    bg={rotated ? 'black' : undefined}
                    color={rotated ? 'white' : undefined}
                    _hover={rotated ? { bg: 'gray.800' } : undefined}
                    flexShrink={0}
                    onClick={onToggleRotate}
                    css={{ _icon: { width: '14px', height: '14px' } }}
                  >
                    <LuRotateCcw />
                  </IconButton>
                </Tooltip>
              )}
              <Button
                size="xs"
                variant="solid"
                bg="black"
                color="white"
                _hover={{ bg: 'gray.800' }}
                flexShrink={0}
                aria-label={FIT_MODE_LABEL[currentFitMode]}
                onClick={cycleFit}
              >
                {FIT_MODE_LABEL[currentFitMode]}
              </Button>
            </HStack>
          </Box>

          {/* Main SVG */}
          <svg
            ref={svgRef}
            width="100%"
            height="100%"
            viewBox={`${view.x} ${view.y} ${view.w} ${view.h}`}
            preserveAspectRatio="xMidYMid meet"
            onMouseDown={onMouseDown}
            onClick={onSvgClick}
            onMouseMove={onSvgMouseMove}
            onMouseLeave={() => setEdgePreviewPoint(null)}
            style={{
              flex: '1 1 auto',
              minHeight: 0,
              width: '100%',
              height: '100%',
              display: 'block',
              touchAction: 'none',
              cursor:
                tool === 'createNode'
                  ? 'crosshair'
                  : grabbing
                    ? 'grabbing'
                    : 'grab',
            }}
          >
            {/* Map image backdrop, positioned via world→SVG matrix */}
            {mapImage && (
              <image
                href={mapImage.url}
                x={0}
                y={0}
                width={mapImage.width}
                height={mapImage.height}
                transform={imageTransform(mapImage, toPx)}
                preserveAspectRatio="none"
                style={{ pointerEvents: 'none' }}
              />
            )}

            {/* Edges */}
            {lifEdges.map((e) => {
              const a = nodeById.get(e.startNodeId);
              const b = nodeById.get(e.endNodeId);
              if (!a || !b) return null;
              const p1 = toPx(a.x, a.y);
              const p2 = toPx(b.x, b.y);
              const isSelected = e.edgeId === selectedEdgeId;
              return (
                <line
                  key={e.edgeId}
                  x1={p1.x}
                  y1={p1.y}
                  x2={p2.x}
                  y2={p2.y}
                  stroke={isSelected ? SELECTED_COLOR : '#cbd5e0'}
                  strokeWidth={
                    isSelected ? VISUAL.edgeWidth + 2 : VISUAL.edgeWidth
                  }
                  strokeLinecap="round"
                  onClick={(event) => {
                    event.stopPropagation();
                    onPickEdge(e.edgeId);
                  }}
                  onMouseDown={(event) => event.stopPropagation()}
                  style={{ cursor: 'pointer' }}
                />
              );
            })}

            {tool === 'createEdge' &&
              edgeStartNodeId &&
              edgePreviewPoint &&
              (() => {
                const start = nodeById.get(edgeStartNodeId);
                if (!start) return null;
                const p1 = toPx(start.x, start.y);
                return (
                  <line
                    x1={p1.x}
                    y1={p1.y}
                    x2={edgePreviewPoint.x}
                    y2={edgePreviewPoint.y}
                    stroke="#805ad5"
                    strokeWidth={VISUAL.edgeWidth}
                    strokeDasharray="8 5"
                    strokeLinecap="round"
                    opacity={0.75}
                    pointerEvents="none"
                  />
                );
              })()}

            {previewNode &&
              (() => {
                const p = toPx(
                  previewNode.nodePosition.x,
                  previewNode.nodePosition.y,
                );
                const anchor = selectedNodeId
                  ? nodeById.get(selectedNodeId)
                  : null;
                const anchorPoint = anchor ? toPx(anchor.x, anchor.y) : null;
                const r = VISUAL.goalNodeRadius;
                return (
                  <g pointerEvents="none" opacity={0.9}>
                    {anchorPoint && (
                      <line
                        x1={anchorPoint.x}
                        y1={anchorPoint.y}
                        x2={p.x}
                        y2={p.y}
                        stroke="#319795"
                        strokeWidth={VISUAL.edgeWidth}
                        strokeDasharray="6 5"
                        strokeLinecap="round"
                        opacity={0.45}
                      />
                    )}
                    <circle
                      cx={p.x}
                      cy={p.y}
                      r={r + 7}
                      fill="#319795"
                      opacity={0.14}
                    />
                    <circle
                      cx={p.x}
                      cy={p.y}
                      r={r}
                      fill="#e6fffa"
                      stroke="#319795"
                      strokeWidth={3}
                      strokeDasharray="5 4"
                    />
                    <text
                      x={p.x}
                      y={p.y - r - 4}
                      textAnchor="middle"
                      fontSize={VISUAL.nodeLabelSize}
                      fontWeight={700}
                      fill="#285e61"
                      style={{ textShadow: TEXT_SHADOW }}
                    >
                      {previewNode.nodeId}
                    </text>
                    <text
                      x={p.x}
                      y={p.y + r + VISUAL.coordLabelSize}
                      textAnchor="middle"
                      fontSize={VISUAL.coordLabelSize}
                      fill="#2c7a7b"
                      style={{ textShadow: TEXT_SHADOW }}
                    >
                      ({previewNode.nodePosition.x.toFixed(2)},{' '}
                      {previewNode.nodePosition.y.toFixed(2)})
                    </text>
                  </g>
                );
              })()}

            {/* Nodes */}
            {nodes.map((n) => {
              const base = toPx(n.x, n.y);
              const isSelected = n.node_id === selectedNodeId;
              const isEdgeStart = n.node_id === edgeStartNodeId;
              const r =
                isSelected || isEdgeStart
                  ? VISUAL.goalNodeRadius
                  : VISUAL.nodeRadius;
              return (
                <g
                  key={n.node_id}
                  onMouseDown={(event) => onNodeMouseDown(event, n.node_id)}
                  onClick={(event) => {
                    event.stopPropagation();
                    handlePickNode(n.node_id);
                  }}
                  style={{
                    cursor: tool === 'createEdge' ? 'copy' : 'pointer',
                  }}
                >
                  {isEdgeStart && (
                    <circle
                      cx={base.x}
                      cy={base.y}
                      r={VISUAL.goalNodeRadius + 5}
                      fill="none"
                      stroke="#805ad5"
                      strokeWidth={2}
                      strokeDasharray="4 3"
                    />
                  )}
                  <circle
                    cx={base.x}
                    cy={base.y}
                    r={r}
                    fill={
                      isSelected || isEdgeStart ? SELECTED_COLOR : '#4a5568'
                    }
                    stroke={
                      isSelected || isEdgeStart ? SELECTED_STROKE : 'none'
                    }
                    strokeWidth={isSelected || isEdgeStart ? 2 : 0}
                  />
                  <text
                    x={base.x}
                    y={base.y - r - 2}
                    textAnchor="middle"
                    fontSize={VISUAL.nodeLabelSize}
                    fontWeight={600}
                    fill="#2d3748"
                    style={{ textShadow: TEXT_SHADOW }}
                  >
                    {n.node_id}
                  </text>
                  <text
                    x={base.x}
                    y={base.y + r + VISUAL.coordLabelSize}
                    textAnchor="middle"
                    fontSize={VISUAL.coordLabelSize}
                    fill="#718096"
                    style={{ textShadow: TEXT_SHADOW }}
                  >
                    ({n.x.toFixed(2)}, {n.y.toFixed(2)})
                  </text>
                </g>
              );
            })}
          </svg>
        </>
      )}
    </Box>
  );
}

export default LifMapCanvas;
