import { memo } from 'react';
import { Handle, Position, type NodeProps } from '@xyflow/react';
import type { LifNode } from '../types';

type LiftNodeData = {
  label: string;
  lifNode: LifNode;
};

const NODE_R = 20;

const baseStyle: React.CSSProperties = {
  width: NODE_R * 2,
  height: NODE_R * 2,
  borderRadius: '50%',
  display: 'flex',
  alignItems: 'center',
  justifyContent: 'center',
  border: '2px solid',
  cursor: 'pointer',
  userSelect: 'none',
  transition: 'background 0.15s',
};

const handleStyle: React.CSSProperties = {
  width: 8,
  height: 8,
  background: '#1abc9c',
  border: '1px solid #16a085',
};

export const LifFlowNode = memo(function LifFlowNode({
  data,
  selected,
}: NodeProps) {
  const nodeData = data as unknown as LiftNodeData;
  const label = nodeData.label ?? '';

  return (
    <div
      style={{
        ...baseStyle,
        background: selected ? '#ff9800' : '#1abc9c',
        borderColor: selected ? '#e65100' : '#16a085',
        boxShadow: selected ? '0 0 0 3px rgba(255,152,0,0.3)' : 'none',
      }}
      title={nodeData.lifNode?.nodeId}
    >
      <Handle type="target" position={Position.Top} style={handleStyle} />
      <Handle type="target" position={Position.Left} style={handleStyle} />
      <Handle type="source" position={Position.Bottom} style={handleStyle} />
      <Handle type="source" position={Position.Right} style={handleStyle} />

      <span
        style={{
          fontSize: 9,
          color: 'white',
          fontWeight: 600,
          textAlign: 'center',
          overflow: 'hidden',
          maxWidth: NODE_R * 2 - 4,
          lineHeight: 1.1,
          padding: '0 2px',
          wordBreak: 'break-all',
        }}
      >
        {label.length > 10 ? `${label.slice(0, 9)}…` : label}
      </span>
    </div>
  );
});
