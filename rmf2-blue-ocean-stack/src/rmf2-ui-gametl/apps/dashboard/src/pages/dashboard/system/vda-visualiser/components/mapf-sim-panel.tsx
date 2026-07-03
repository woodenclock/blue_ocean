// Inline panel (Logs tab's sibling "MAPF sim" tab): preview CBS MAPF for
// queued goals, then scrub timesteps. Runs its plan on mount and clears the
// map ghost overlay on unmount (the parent Tabs.Root uses
// lazyMount/unmountOnExit, so mount/unmount tracks tab visibility).
import { useCallback, useEffect, useMemo, useState } from 'react';
import {
  Badge,
  Box,
  Button,
  HStack,
  IconButton,
  Slider,
  Stack,
  Table,
  Text,
} from '@chakra-ui/react';

import { ROBOT_COLOR_BY_ID } from './constants';
import { fetchMapfPlans } from '../master-api';
import { buildMapfTimeline, formatMapfPlanPath } from '../mapf-sim';
import { previewMapf } from '../scheduler-api';
import type {
  MapfPlansByRobot,
  MapfSimFrame,
  RobotGoal,
  SchedulerRobotId,
} from '../types';

const PLAY_MS = 700;

export interface MapfSimPanelProps {
  goals: Partial<Record<SchedulerRobotId, RobotGoal>>;
  onFrameChange: (frame: MapfSimFrame | null) => void;
}

function queuedMapTasks(
  goals: Partial<Record<SchedulerRobotId, RobotGoal>>,
): { robot_id: SchedulerRobotId; goal_location: string }[] {
  return (Object.keys(goals) as SchedulerRobotId[])
    .filter((id) => {
      const goal = goals[id];
      return goal != null && !goal.pickRackMode && goal.node.includes(',');
    })
    .map((id) => ({
      robot_id: id,
      goal_location: goals[id]!.node,
    }));
}

function applyPlans(plans: MapfPlansByRobot) {
  const ids = Object.keys(plans);
  const { frames: built } = buildMapfTimeline(plans);
  const paths: Record<string, string> = {};
  for (const id of ids) {
    paths[id] = formatMapfPlanPath(plans[id]);
  }
  return { ids, built, paths };
}

export function MapfSimPanel({ goals, onFrameChange }: MapfSimPanelProps) {
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [frames, setFrames] = useState<MapfSimFrame[]>([]);
  const [robotIds, setRobotIds] = useState<SchedulerRobotId[]>([]);
  const [pathByRobot, setPathByRobot] = useState<Record<string, string>>({});
  const [step, setStep] = useState(0);
  const [playing, setPlaying] = useState(false);
  const [source, setSource] = useState<'preview' | 'stored' | null>(null);

  const pendingTasks = useMemo(() => queuedMapTasks(goals), [goals]);

  const loadFromPlans = useCallback(
    (
      plans: MapfPlansByRobot,
      from: 'preview' | 'stored',
      planError?: string | null,
    ) => {
      const { ids, built, paths } = applyPlans(plans);
      setPathByRobot(paths);
      setRobotIds(ids);
      setFrames(built);
      setStep(0);
      setSource(from);
      if (ids.length === 0) {
        setError(planError ?? 'No MAPF plan returned.');
        onFrameChange(null);
      } else {
        setError(planError ?? null);
      }
    },
    [onFrameChange],
  );

  const runPlan = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      if (pendingTasks.length > 0) {
        const { plans, error: planError } = await previewMapf(
          pendingTasks,
          false,
        );
        loadFromPlans(plans, 'preview', planError);
        return;
      }
      const stored = await fetchMapfPlans();
      loadFromPlans(stored, 'stored');
    } catch (err) {
      setError(String(err));
      setFrames([]);
      setRobotIds([]);
      setPathByRobot({});
      setSource(null);
      onFrameChange(null);
    } finally {
      setLoading(false);
    }
  }, [pendingTasks, loadFromPlans, onFrameChange]);

  // Run once on mount (tab open); clear the map ghost overlay on unmount
  // (tab closed) — mirrors the old Popover's open/close behaviour.
  useEffect(() => {
    void runPlan();
    return () => onFrameChange(null);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const current = frames[step] ?? null;

  useEffect(() => {
    onFrameChange(current);
  }, [current, onFrameChange]);

  useEffect(() => {
    if (!playing || frames.length === 0) return;
    const id = window.setInterval(() => {
      setStep((s) => {
        if (s >= frames.length - 1) {
          setPlaying(false);
          return s;
        }
        return s + 1;
      });
    }, PLAY_MS);
    return () => window.clearInterval(id);
  }, [playing, frames.length]);

  const goalHint =
    pendingTasks.length > 0
      ? pendingTasks.map((t) => `${t.robot_id}→${t.goal_location}`).join(', ')
      : null;

  return (
    <Stack gap={3}>
      <HStack justify="space-between" align="start" gap={2}>
        <Stack gap={0}>
          <Text fontSize="sm" fontWeight="semibold">
            MAPF timestep preview
          </Text>
          <Text fontSize="xs" color="gray.500">
            Plans queued goals with CBS (no dispatch). Apply runs the same plan
            for real.
          </Text>
        </Stack>
        <Button
          size="2xs"
          variant="solid"
          colorPalette="green"
          loading={loading}
          onClick={() => void runPlan()}
        >
          Plan
        </Button>
      </HStack>
      {goalHint && (
        <Text fontSize="xs" color="gray.600">
          Queued: {goalHint}
        </Text>
      )}

      {error && (
        <Text fontSize="xs" color="red.500">
          {error}
        </Text>
      )}
      {frames.length === 0 && !loading && !error && (
        <Text fontSize="sm" color="gray.500">
          Arm each robot, click goal nodes on the map, then open MAPF sim or
          press Plan. With no queued goals, stored plans are used after Apply.
        </Text>
      )}
      {frames.length > 0 && current && (
        <Stack gap={3}>
          <HStack justify="space-between">
            <Badge colorPalette="green" variant="subtle">
              t={current.time}
              {source === 'preview' ? ' · preview' : ' · stored'}
            </Badge>
            <Text fontSize="xs" color="gray.500">
              step {step + 1} / {frames.length}
            </Text>
          </HStack>

          <HStack gap={1}>
            <IconButton
              aria-label="Step back"
              size="xs"
              variant="outline"
              disabled={step <= 0}
              onClick={() => setStep((s) => Math.max(0, s - 1))}
            >
              ‹
            </IconButton>
            <IconButton
              aria-label={playing ? 'Pause' : 'Play'}
              size="xs"
              variant="solid"
              colorPalette="green"
              onClick={() => {
                if (step >= frames.length - 1) setStep(0);
                setPlaying((p) => !p);
              }}
            >
              {playing ? '❚❚' : '▶'}
            </IconButton>
            <IconButton
              aria-label="Step forward"
              size="xs"
              variant="outline"
              disabled={step >= frames.length - 1}
              onClick={() => setStep((s) => Math.min(frames.length - 1, s + 1))}
            >
              ›
            </IconButton>
            <Box flex="1" px={2}>
              <Slider.Root
                min={0}
                max={Math.max(0, frames.length - 1)}
                step={1}
                value={[step]}
                onValueChange={(e) => {
                  setPlaying(false);
                  setStep(e.value[0] ?? 0);
                }}
              >
                <Slider.Control>
                  <Slider.Track>
                    <Slider.Range />
                  </Slider.Track>
                  <Slider.Thumb index={0} />
                </Slider.Control>
              </Slider.Root>
            </Box>
          </HStack>

          <Table.Root size="sm" variant="outline">
            <Table.Header>
              <Table.Row>
                <Table.ColumnHeader>Robot</Table.ColumnHeader>
                <Table.ColumnHeader>Node</Table.ColumnHeader>
                <Table.ColumnHeader>State</Table.ColumnHeader>
              </Table.Row>
            </Table.Header>
            <Table.Body>
              {robotIds.map((robotId) => {
                const pos = current.robots[robotId];
                if (!pos) return null;
                const color =
                  ROBOT_COLOR_BY_ID[robotId as SchedulerRobotId] ?? '#718096';
                return (
                  <Table.Row key={robotId}>
                    <Table.Cell>
                      <HStack gap={1}>
                        <Box w="8px" h="8px" borderRadius="full" bg={color} />
                        <Text fontSize="xs">{robotId}</Text>
                      </HStack>
                    </Table.Cell>
                    <Table.Cell fontFamily="mono" fontSize="xs">
                      {pos.nodeId}
                    </Table.Cell>
                    <Table.Cell fontSize="xs">
                      {pos.holding ? (
                        <Text color="orange.600" title={pos.blockerNote}>
                          hold
                        </Text>
                      ) : (
                        <Text color="gray.500">move</Text>
                      )}
                    </Table.Cell>
                  </Table.Row>
                );
              })}
            </Table.Body>
          </Table.Root>

          {Object.keys(pathByRobot).length > 0 && (
            <Stack gap={1}>
              <Text fontSize="xs" fontWeight="semibold" color="gray.600">
                Full plans
              </Text>
              {Object.entries(pathByRobot).map(([id, path]) => (
                <Text key={id} fontSize="xs" color="gray.600">
                  <Text as="span" fontWeight="medium">
                    {id}:
                  </Text>{' '}
                  {path}
                </Text>
              ))}
            </Stack>
          )}
        </Stack>
      )}
    </Stack>
  );
}

export default MapfSimPanel;
