// Point-and-click toolbar: arm robots, queue goals on the map, Apply dispatches
// all pending robots. Clear resets the local queue; Stop & reset all stops every
// robot and clears mission/MAPF state.
import { Box, Button, HStack } from '@chakra-ui/react';

import type { RobotGoal, SchedulerRobotId } from '../types';

interface NavControlsProps {
  goals: Partial<Record<SchedulerRobotId, RobotGoal>>;
  applying: boolean;
  onApply: () => void;
  onClear: () => void;
  onStopResetAll: () => void;
  stopResetAllLoading: boolean;
}

const dipProps = {
  bg: 'bg.muted',
  borderRadius: 'lg',
  p: '1',
  w: '100%',
  boxShadow: 'inset 0 1px 2px rgba(0, 0, 0, 0.06)',
} as const;

function dipSegmentProps(active: boolean, color?: 'red' | 'blue', flex = 1) {
  const activeColor =
    color === 'red' ? 'red.fg' : color === 'blue' ? 'blue.fg' : 'fg';
  return {
    flex,
    size: 'sm' as const,
    variant: 'ghost' as const,
    borderRadius: 'md',
    fontWeight: 'medium',
    minH: '36px',
    px: 3,
    whiteSpace: 'nowrap' as const,
    bg: active ? 'bg' : 'transparent',
    boxShadow: active ? 'sm' : 'none',
    color: active ? activeColor : 'fg.muted',
    _hover: active
      ? { bg: 'bg', boxShadow: 'sm' }
      : { bg: 'bg', boxShadow: 'sm', color: activeColor },
    _disabled: {
      opacity: 0.45,
      cursor: 'not-allowed',
      bg: 'transparent',
      boxShadow: 'none',
      _hover: { bg: 'transparent', boxShadow: 'none' },
    },
  };
}

export function NavControls({
  goals,
  applying,
  onApply,
  onClear,
  onStopResetAll,
  stopResetAllLoading,
}: NavControlsProps) {
  const hasPending = Object.values(goals).some(
    (goal) => goal != null && !goal.applied,
  );
  const stopResetAllEnabled = !applying;

  return (
    <Box {...dipProps}>
      <HStack gap="1" w="100%">
        <Button
          {...dipSegmentProps(stopResetAllEnabled, 'red', 1.6)}
          loading={stopResetAllLoading}
          disabled={!stopResetAllEnabled}
          onClick={onStopResetAll}
        >
          Stop & reset all
        </Button>
        <Button {...dipSegmentProps(false, undefined, 0.85)} onClick={onClear}>
          Clear
        </Button>
        <Button
          {...dipSegmentProps(hasPending && !applying, 'blue', 0.85)}
          loading={applying}
          disabled={!hasPending}
          onClick={onApply}
        >
          Apply
        </Button>
      </HStack>
    </Box>
  );
}

export default NavControls;
