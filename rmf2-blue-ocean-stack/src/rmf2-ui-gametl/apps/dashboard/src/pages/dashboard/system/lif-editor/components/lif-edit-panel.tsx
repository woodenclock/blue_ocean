import { useEffect, useState } from 'react';
import {
  Box,
  Button,
  Card,
  HStack,
  Input,
  Separator,
  Select,
  Stack,
  Text,
  createListCollection,
} from '@chakra-ui/react';
import { LuCirclePlus, LuTrash2, LuImage, LuSave, LuX } from 'react-icons/lu';
import type { LifEdge, LifNode } from '../types';
import type {
  AdjacentNodePreview,
  AdjacentNodeCreateOption,
  ImageOverlay,
} from '../lif-editor-scene';

// ---- Node properties form ----

function limitDecimalPlaces(value: string, maxDecimals = 2): string {
  const [whole, decimal] = value.split('.');
  if (decimal == null) return value;
  return `${whole}.${decimal.slice(0, maxDecimals)}`;
}

type NodeFormProps = {
  node: LifNode;
  nodes: LifNode[];
  createOptions: AdjacentNodeCreateOption[];
  showCreateAdjacentNode: boolean;
  onSave: (updated: LifNode, originalNodeId: string) => void;
  onCreateAdjacentNode: (
    option: AdjacentNodeCreateOption,
    adjustedCoordinate: number,
  ) => void;
  onAdjacentNodePreviewChange: (preview: AdjacentNodePreview | null) => void;
  onDelete: () => void;
};

function NodeForm({
  node,
  nodes,
  createOptions,
  showCreateAdjacentNode,
  onSave,
  onCreateAdjacentNode,
  onAdjacentNodePreviewChange,
  onDelete,
}: NodeFormProps) {
  const [draft, setDraft] = useState(node);
  const [selectedCreateNodeId, setSelectedCreateNodeId] = useState('');
  const [newNodeCoordinate, setNewNodeCoordinate] = useState('');
  const nodeHeaderColor = { base: 'gray.900', _dark: 'white' };
  const nodeLabelColor = { base: 'gray.700', _dark: 'whiteAlpha.900' };

  useEffect(() => {
    setDraft(node);
  }, [node]);

  const update = (patch: Partial<LifNode>) => {
    setDraft((current) => ({ ...current, ...patch }));
  };
  const createCollection = createListCollection({
    items: createOptions.map((option) => ({
      label: option.label,
      value: option.nodeId,
    })),
  });
  const createOptionsKey = createOptions
    .map((option) => option.nodeId)
    .join('|');
  const selectedCreateOption =
    createOptions.find((option) => option.nodeId === selectedCreateNodeId) ??
    null;
  const selectedCreateOptionId = selectedCreateOption?.nodeId ?? null;
  const selectedCreateOptionAxis = selectedCreateOption?.adjustableAxis ?? null;
  const selectedCreateOptionX = selectedCreateOption?.defaultPosition.x ?? null;
  const selectedCreateOptionY = selectedCreateOption?.defaultPosition.y ?? null;
  const selectedCreateDefaultCoordinate =
    selectedCreateOption && selectedCreateOptionAxis
      ? selectedCreateOption.defaultPosition[selectedCreateOptionAxis]
      : null;

  useEffect(() => {
    setSelectedCreateNodeId(createOptions[0]?.nodeId ?? '');
  }, [createOptionsKey, node.nodeId]);

  useEffect(() => {
    if (selectedCreateDefaultCoordinate == null) {
      setNewNodeCoordinate('');
      return;
    }
    setNewNodeCoordinate(selectedCreateDefaultCoordinate.toFixed(2));
  }, [
    node.nodeId,
    selectedCreateDefaultCoordinate,
    selectedCreateOptionAxis,
    selectedCreateOptionId,
  ]);

  const trimmedNodeId = draft.nodeId.trim();
  const trimmedNodeName = draft.nodeName.trim() || trimmedNodeId;
  const duplicateNodeId = nodes.some(
    (candidate) =>
      candidate.nodeId === trimmedNodeId && candidate.nodeId !== node.nodeId,
  );
  const duplicateNodeName = nodes.some(
    (candidate) =>
      (candidate.nodeName || candidate.nodeId) === trimmedNodeName &&
      candidate.nodeId !== node.nodeId,
  );
  const canSave =
    trimmedNodeId.length > 0 && !duplicateNodeId && !duplicateNodeName;

  const save = () => {
    if (!canSave) return;
    onSave(
      {
        ...draft,
        nodeId: trimmedNodeId,
        nodeName: trimmedNodeName,
      },
      node.nodeId,
    );
  };
  const createCoordinate = Number(newNodeCoordinate);
  const selectedCreateAxis = selectedCreateOption?.adjustableAxis;
  const selectedCreateAnchorCoordinate = selectedCreateAxis
    ? node.nodePosition[selectedCreateAxis]
    : 0;
  const coordinateDirectionValid =
    !!selectedCreateOption &&
    Number.isFinite(createCoordinate) &&
    (selectedCreateOption.axisDirection > 0
      ? createCoordinate > selectedCreateAnchorCoordinate
      : createCoordinate < selectedCreateAnchorCoordinate);
  const canCreateAdjacentNode =
    !!selectedCreateOption &&
    newNodeCoordinate.trim() !== '' &&
    Number.isFinite(createCoordinate) &&
    coordinateDirectionValid;
  useEffect(() => {
    if (!showCreateAdjacentNode || !selectedCreateOption) {
      onAdjacentNodePreviewChange(null);
      return;
    }
    if (
      newNodeCoordinate.trim() === '' ||
      !Number.isFinite(createCoordinate) ||
      !coordinateDirectionValid
    ) {
      onAdjacentNodePreviewChange(null);
      return;
    }
    onAdjacentNodePreviewChange({
      nodeId: selectedCreateOption.nodeId,
      nodePosition: {
        ...selectedCreateOption.defaultPosition,
        [selectedCreateOption.adjustableAxis]: createCoordinate,
      },
    });
  }, [
    coordinateDirectionValid,
    createCoordinate,
    newNodeCoordinate,
    onAdjacentNodePreviewChange,
    selectedCreateOptionAxis,
    selectedCreateOptionId,
    selectedCreateOptionX,
    selectedCreateOptionY,
    showCreateAdjacentNode,
  ]);

  const createAdjacentNode = () => {
    if (!selectedCreateOption || !canCreateAdjacentNode) return;
    onCreateAdjacentNode(selectedCreateOption, createCoordinate);
  };

  return (
    <Stack gap={3}>
      {showCreateAdjacentNode && (
        <>
          <Stack gap={3}>
            <Text fontSize="sm" fontWeight="bold" color={nodeHeaderColor}>
              Create adjacent node
            </Text>

            {createOptions.length === 0 ? (
              <Text fontSize="xs" color="fg.muted">
                No adjacent coordinate nodes are available from {node.nodeId}.
              </Text>
            ) : (
              <>
                <Stack gap={1}>
                  <Text
                    fontSize="xs"
                    color={nodeLabelColor}
                    fontWeight="medium"
                  >
                    New node
                  </Text>
                  <Select.Root
                    collection={createCollection}
                    value={selectedCreateNodeId ? [selectedCreateNodeId] : []}
                    onValueChange={({ value }) =>
                      setSelectedCreateNodeId(value[0] ?? '')
                    }
                    size="sm"
                  >
                    <Select.Trigger>
                      <Select.ValueText />
                    </Select.Trigger>
                    <Select.Content>
                      {createCollection.items.map((item) => (
                        <Select.Item key={item.value} item={item}>
                          {item.label}
                        </Select.Item>
                      ))}
                    </Select.Content>
                  </Select.Root>
                </Stack>

                {selectedCreateOption && (
                  <Stack gap={1}>
                    <Text
                      fontSize="xs"
                      color={nodeLabelColor}
                      fontWeight="medium"
                    >
                      Adjust {selectedCreateOption.adjustableAxis.toUpperCase()}
                    </Text>
                    <Input
                      size="sm"
                      type="number"
                      step="0.01"
                      min={
                        selectedCreateOption.axisDirection > 0
                          ? selectedCreateAnchorCoordinate + 0.01
                          : undefined
                      }
                      max={
                        selectedCreateOption.axisDirection < 0
                          ? selectedCreateAnchorCoordinate - 0.01
                          : undefined
                      }
                      value={newNodeCoordinate}
                      onChange={(event) =>
                        setNewNodeCoordinate(
                          limitDecimalPlaces(event.target.value),
                        )
                      }
                      aria-invalid={!canCreateAdjacentNode}
                      borderColor={
                        !canCreateAdjacentNode ? 'red.500' : undefined
                      }
                    />
                    <Text fontSize="xs" color="fg.muted">
                      {selectedCreateOption.adjustableAxis === 'x'
                        ? `Y stays fixed at ${selectedCreateOption.defaultPosition.y.toFixed(2)}.`
                        : `X stays fixed at ${selectedCreateOption.defaultPosition.x.toFixed(2)}.`}
                    </Text>
                    {!coordinateDirectionValid &&
                      newNodeCoordinate.trim() !== '' && (
                        <Text fontSize="xs" color="red.500">
                          {selectedCreateOption.adjustableAxis.toUpperCase()}{' '}
                          must be{' '}
                          {selectedCreateOption.axisDirection > 0
                            ? 'greater than'
                            : 'less than'}{' '}
                          {selectedCreateAnchorCoordinate.toFixed(2)}.
                        </Text>
                      )}
                  </Stack>
                )}

                <Button
                  size="sm"
                  colorPalette="teal"
                  disabled={!canCreateAdjacentNode}
                  onClick={createAdjacentNode}
                >
                  <LuCirclePlus />
                  Create node
                </Button>
              </>
            )}
          </Stack>

          <Separator />
        </>
      )}

      <HStack justify="space-between">
        <Text fontSize="sm" fontWeight="bold" color={nodeHeaderColor}>
          Node properties
        </Text>
        <Button size="xs" variant="ghost" colorPalette="red" onClick={onDelete}>
          <LuTrash2 />
          Delete
        </Button>
      </HStack>

      <Stack gap={1}>
        <Text fontSize="xs" color={nodeLabelColor} fontWeight="medium">
          ID
        </Text>
        <Input
          size="sm"
          value={draft.nodeId}
          onChange={(e) => update({ nodeId: e.target.value })}
          fontFamily="mono"
          aria-invalid={duplicateNodeId || trimmedNodeId.length === 0}
          borderColor={
            duplicateNodeId || trimmedNodeId.length === 0
              ? 'red.500'
              : undefined
          }
        />
        {trimmedNodeId.length === 0 && (
          <Text fontSize="xs" color="red.500">
            Node ID is required.
          </Text>
        )}
        {duplicateNodeId && (
          <Text fontSize="xs" color="red.500">
            Another node already uses this ID.
          </Text>
        )}
      </Stack>

      <Stack gap={1}>
        <Text fontSize="xs" color={nodeLabelColor} fontWeight="medium">
          Name
        </Text>
        <Input
          size="sm"
          value={draft.nodeName}
          onChange={(e) => update({ nodeName: e.target.value })}
          aria-invalid={duplicateNodeName}
          borderColor={duplicateNodeName ? 'red.500' : undefined}
        />
        {duplicateNodeName && (
          <Text fontSize="xs" color="red.500">
            Another node already uses this name.
          </Text>
        )}
      </Stack>

      <Card.Root variant="outline" size="sm">
        <Card.Body py={2} gap={3}>
          <Text fontSize="xs" fontWeight="semibold" color={nodeLabelColor}>
            Position (meters)
          </Text>
          <HStack gap={2}>
            <Stack gap={1} flex={1}>
              <Text fontSize="xs" color={nodeLabelColor}>
                X
              </Text>
              <Input
                size="sm"
                type="number"
                step="0.01"
                value={draft.nodePosition.x.toFixed(2)}
                onChange={(e) => {
                  const x = parseFloat(e.target.value);
                  if (Number.isFinite(x))
                    update({ nodePosition: { ...draft.nodePosition, x } });
                }}
              />
            </Stack>
            <Stack gap={1} flex={1}>
              <Text fontSize="xs" color={nodeLabelColor}>
                Y
              </Text>
              <Input
                size="sm"
                type="number"
                step="0.01"
                value={draft.nodePosition.y.toFixed(2)}
                onChange={(e) => {
                  const y = parseFloat(e.target.value);
                  if (Number.isFinite(y))
                    update({ nodePosition: { ...draft.nodePosition, y } });
                }}
              />
            </Stack>
          </HStack>
        </Card.Body>
      </Card.Root>

      <Stack gap={1}>
        <Text fontSize="xs" color={nodeLabelColor} fontWeight="medium">
          Map ID
        </Text>
        <Input
          size="sm"
          value={draft.mapId}
          onChange={(e) => update({ mapId: e.target.value })}
        />
      </Stack>

      {node.vehicleTypeNodeProperties &&
        node.vehicleTypeNodeProperties.length > 0 && (
          <Stack gap={1}>
            <Text fontSize="xs" color={nodeLabelColor} fontWeight="medium">
              Vehicle types
            </Text>
            {node.vehicleTypeNodeProperties.map((p) => (
              <Text key={p.vehicleTypeId} fontSize="xs" fontFamily="mono">
                {p.vehicleTypeId}
              </Text>
            ))}
          </Stack>
        )}

      <Button size="sm" colorPalette="blue" disabled={!canSave} onClick={save}>
        <LuSave />
        Save node
      </Button>
    </Stack>
  );
}

// ---- Edge properties form ----

type EdgeFormProps = {
  edge: LifEdge;
  nodes: LifNode[];
  onChange: (updated: LifEdge) => void;
  onDelete: () => void;
};

function EdgeForm({ edge, nodes, onChange, onDelete }: EdgeFormProps) {
  const nodeNameMap = new Map(
    nodes.map((n) => [n.nodeId, n.nodeName || n.nodeId]),
  );

  return (
    <Stack gap={3}>
      <HStack justify="space-between">
        <Text fontSize="sm" fontWeight="bold">
          Edge properties
        </Text>
        <Button size="xs" variant="ghost" colorPalette="red" onClick={onDelete}>
          <LuTrash2 />
          Delete
        </Button>
      </HStack>

      <Stack gap={1}>
        <Text fontSize="xs" color="fg.muted" fontWeight="medium">
          ID
        </Text>
        <Input
          size="sm"
          value={edge.edgeId}
          readOnly
          fontFamily="mono"
          bg="bg.subtle"
        />
      </Stack>

      <Stack gap={1}>
        <Text fontSize="xs" color="fg.muted" fontWeight="medium">
          Name
        </Text>
        <Input
          size="sm"
          value={edge.edgeName}
          onChange={(e) => onChange({ ...edge, edgeName: e.target.value })}
        />
      </Stack>

      <Card.Root variant="outline" size="sm">
        <Card.Body py={2} gap={2}>
          <Stack gap={0.5}>
            <Text fontSize="xs" color="fg.muted">
              Start node
            </Text>
            <Text fontSize="sm" fontFamily="mono">
              {nodeNameMap.get(edge.startNodeId) ?? edge.startNodeId}
            </Text>
          </Stack>
          <Stack gap={0.5}>
            <Text fontSize="xs" color="fg.muted">
              End node
            </Text>
            <Text fontSize="sm" fontFamily="mono">
              {nodeNameMap.get(edge.endNodeId) ?? edge.endNodeId}
            </Text>
          </Stack>
        </Card.Body>
      </Card.Root>

      {edge.vehicleTypeEdgeProperties?.map((p) => (
        <Card.Root key={p.vehicleTypeId} size="sm" variant="outline">
          <Card.Body gap={2} py={2}>
            <Text fontSize="xs" fontWeight="semibold">
              {p.vehicleTypeId}
            </Text>
            <Stack gap={1}>
              <Text fontSize="xs" color="fg.muted">
                Orientation (°)
              </Text>
              <Input
                size="sm"
                type="number"
                step="1"
                value={p.vehicleOrientation ?? 0}
                onChange={(e) => {
                  const updated = {
                    ...edge,
                    vehicleTypeEdgeProperties:
                      edge.vehicleTypeEdgeProperties!.map((ep) =>
                        ep.vehicleTypeId === p.vehicleTypeId
                          ? {
                              ...ep,
                              vehicleOrientation: parseFloat(e.target.value),
                            }
                          : ep,
                      ),
                  };
                  onChange(updated);
                }}
              />
            </Stack>
          </Card.Body>
        </Card.Root>
      ))}
    </Stack>
  );
}

// ---- Image overlay settings ----

type ImageOverlaySettingsProps = {
  overlay: ImageOverlay | null;
  onChange: (o: ImageOverlay | null) => void;
  onImportImage: () => void;
};

function ImageOverlaySettings({
  overlay,
  onChange,
  onImportImage,
}: ImageOverlaySettingsProps) {
  return (
    <Stack gap={2}>
      <HStack justify="space-between">
        <Text fontSize="xs" fontWeight="semibold" color="fg.muted">
          Background image
        </Text>
        {overlay && (
          <Button
            size="xs"
            variant="ghost"
            colorPalette="red"
            onClick={() => onChange(null)}
          >
            <LuX />
            Clear
          </Button>
        )}
      </HStack>

      {!overlay ? (
        <Button size="sm" variant="outline" onClick={onImportImage}>
          <LuImage />
          Import image
        </Button>
      ) : (
        <Stack gap={3}>
          <Stack gap={1}>
            <HStack justify="space-between">
              <Text fontSize="xs" color="fg.muted">
                Opacity
              </Text>
              <Text fontSize="xs" color="fg" fontWeight="medium">
                {Math.round(overlay.opacity * 100)}%
              </Text>
            </HStack>
            <input
              type="range"
              min={0}
              max={1}
              step={0.01}
              value={overlay.opacity}
              style={{ width: '100%', accentColor: '#1abc9c' }}
              onChange={(e) =>
                onChange({ ...overlay, opacity: parseFloat(e.target.value) })
              }
            />
          </Stack>

          <Stack gap={1}>
            <HStack justify="space-between">
              <Text fontSize="xs" color="fg.muted">
                Scale
              </Text>
              <Text fontSize="xs" color="fg" fontWeight="medium">
                {overlay.scale.toFixed(3)}×
              </Text>
            </HStack>
            <input
              type="range"
              min={0.01}
              max={5}
              step={0.01}
              value={overlay.scale}
              style={{ width: '100%', accentColor: '#1abc9c' }}
              onChange={(e) =>
                onChange({ ...overlay, scale: parseFloat(e.target.value) })
              }
            />
          </Stack>

          <HStack gap={2}>
            <Stack gap={1} flex={1}>
              <Text fontSize="xs" color="fg.muted">
                Offset X
              </Text>
              <Input
                size="sm"
                type="number"
                step="1"
                value={Math.round(overlay.x)}
                onChange={(e) => {
                  const x = parseInt(e.target.value, 10);
                  if (Number.isFinite(x)) onChange({ ...overlay, x });
                }}
              />
            </Stack>
            <Stack gap={1} flex={1}>
              <Text fontSize="xs" color="fg.muted">
                Offset Y
              </Text>
              <Input
                size="sm"
                type="number"
                step="1"
                value={Math.round(overlay.y)}
                onChange={(e) => {
                  const y = parseInt(e.target.value, 10);
                  if (Number.isFinite(y)) onChange({ ...overlay, y });
                }}
              />
            </Stack>
          </HStack>

          <Text fontSize="xs" color="fg.subtle">
            {overlay.naturalW} × {overlay.naturalH} px
          </Text>

          <Button size="sm" variant="outline" onClick={onImportImage}>
            <LuImage />
            Replace image
          </Button>
        </Stack>
      )}
    </Stack>
  );
}

// ---- Main panel ----

type LifEditPanelProps = {
  selectedNode: LifNode | null;
  selectedEdge: LifEdge | null;
  lifNodes: LifNode[];
  adjacentNodeCreateOptions: AdjacentNodeCreateOption[];
  showCreateAdjacentNode: boolean;
  onNodeSave: (updated: LifNode, originalNodeId: string) => void;
  onCreateAdjacentNode: (
    option: AdjacentNodeCreateOption,
    adjustedCoordinate: number,
  ) => void;
  onAdjacentNodePreviewChange: (preview: AdjacentNodePreview | null) => void;
  onEdgeUpdate: (updated: LifEdge) => void;
  onDeleteNode: () => void;
  onDeleteEdge: () => void;
  imageOverlay: ImageOverlay | null;
  onImageOverlayChange: (o: ImageOverlay | null) => void;
  onImportImage: () => void;
};

export function LifEditPanel({
  selectedNode,
  selectedEdge,
  lifNodes,
  adjacentNodeCreateOptions,
  showCreateAdjacentNode,
  onNodeSave,
  onCreateAdjacentNode,
  onAdjacentNodePreviewChange,
  onEdgeUpdate,
  onDeleteNode,
  onDeleteEdge,
  imageOverlay,
  onImageOverlayChange,
  onImportImage,
}: LifEditPanelProps) {
  const hasSelection = !!(selectedNode || selectedEdge);

  return (
    <Box h="full" display="flex" flexDir="column" overflow="hidden">
      {/* Header */}
      <Box
        px={3}
        py={2}
        borderBottomWidth="1px"
        borderColor="border.subtle"
        flexShrink={0}
        bg="bg.subtle"
      >
        <Text fontSize="sm" fontWeight="semibold">
          {selectedNode ? 'Node' : selectedEdge ? 'Edge' : 'Properties'}
        </Text>
      </Box>

      {/* Scrollable body */}
      <Box flex={1} overflowY="auto" px={3} py={3}>
        {selectedNode && (
          <NodeForm
            node={selectedNode}
            nodes={lifNodes}
            createOptions={adjacentNodeCreateOptions}
            showCreateAdjacentNode={showCreateAdjacentNode}
            onSave={onNodeSave}
            onCreateAdjacentNode={onCreateAdjacentNode}
            onAdjacentNodePreviewChange={onAdjacentNodePreviewChange}
            onDelete={onDeleteNode}
          />
        )}

        {selectedEdge && !selectedNode && (
          <EdgeForm
            edge={selectedEdge}
            nodes={lifNodes}
            onChange={onEdgeUpdate}
            onDelete={onDeleteEdge}
          />
        )}

        {!hasSelection && (
          <Stack gap={2} pb={2}>
            <Text fontSize="xs" color="fg.muted">
              Click a node or edge to edit its properties.
            </Text>
            <Text fontSize="xs" color="fg.muted">
              <Text as="span" fontWeight="semibold">
                Create node:
              </Text>{' '}
              select a coordinate node, then choose an available adjacent node.
            </Text>
            <Text fontSize="xs" color="fg.muted">
              <Text as="span" fontWeight="semibold">
                Connect:
              </Text>{' '}
              select the branch tool, then click two nodes.
            </Text>
          </Stack>
        )}

        {/* Always-visible image overlay section */}
        <Separator my={3} />
        <ImageOverlaySettings
          overlay={imageOverlay}
          onChange={onImageOverlayChange}
          onImportImage={onImportImage}
        />
      </Box>
    </Box>
  );
}
