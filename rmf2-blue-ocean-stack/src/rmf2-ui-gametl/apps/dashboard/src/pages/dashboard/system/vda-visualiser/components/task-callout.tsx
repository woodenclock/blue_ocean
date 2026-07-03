// Live navigation activity — tasks in progress and navigation conflicts.
// Inline beside Order queue; section title matches OrderQueuePanel.
import { Box, HStack, Stack, Text } from '@chakra-ui/react';

import { ConflictBanner } from './conflict-banner';

interface ActivityConflict {
  detail: string;
  robotId: string;
  goalNode?: string;
  clearing: boolean;
  onClear: () => void;
  onDismiss: () => void;
}

interface TaskCalloutProps {
  tasks: string[];
  variant?: 'standalone' | 'inline';
  conflict?: ActivityConflict | null;
}

function LiveDot() {
  return (
    <Box position="relative" w="10px" h="10px" flexShrink={0} mt="3px">
      <Box
        position="absolute"
        inset="0"
        borderRadius="full"
        bg="teal.400"
        animation="ping 1.5s cubic-bezier(0, 0, 0.2, 1) infinite"
      />
      <Box
        position="absolute"
        inset="0"
        borderRadius="full"
        bg="teal.500"
        animation="pulse 2s cubic-bezier(0.4, 0, 0.6, 1) infinite"
      />
    </Box>
  );
}

function ActivityBody({ tasks }: { tasks: string[] }) {
  return (
    <HStack gap={3} align="flex-start">
      <LiveDot />
      <Stack gap={0}>
        <Text fontSize="sm" fontWeight="medium">
          {tasks.join('  ·  ')}
        </Text>
        <Text fontSize="xs" color="gray.500">
          Robots are on the move
        </Text>
      </Stack>
    </HStack>
  );
}

export function TaskCallout({
  tasks,
  variant = 'inline',
  conflict = null,
}: TaskCalloutProps) {
  if (variant === 'standalone') {
    if (tasks.length === 0) return null;
    return (
      <HStack
        gap={3}
        px={4}
        py={3}
        borderWidth="1px"
        borderColor="teal.300"
        bg="teal.50"
        borderRadius="lg"
        borderLeftWidth="4px"
        borderLeftColor="teal.500"
      >
        <LiveDot />
        <Stack gap={0}>
          <Text fontSize="sm" fontWeight="semibold" color="teal.700">
            Task in progress — robots are on the move
          </Text>
          <Text fontSize="xs" color="teal.700">
            {tasks.join('  ·  ')}
          </Text>
        </Stack>
      </HStack>
    );
  }

  return (
    <Stack gap={2} align="flex-start" w="100%">
      <Text fontSize="sm" fontWeight="semibold">
        Activity
      </Text>
      <Stack gap={2} align="stretch" w="100%">
        {conflict && (
          <ConflictBanner
            detail={conflict.detail}
            robotId={conflict.robotId}
            goalNode={conflict.goalNode}
            clearing={conflict.clearing}
            onClear={conflict.onClear}
            onDismiss={conflict.onDismiss}
            variant="inline"
          />
        )}
        {tasks.length > 0 ? (
          <Box
            borderWidth="1px"
            borderColor="border.subtle"
            borderRadius="md"
            px={3}
            py={2}
            minW="180px"
            w="fit-content"
            maxW="100%"
          >
            <ActivityBody tasks={tasks} />
          </Box>
        ) : (
          !conflict && (
            <Text fontSize="sm" color="gray.500">
              No active tasks.
            </Text>
          )
        )}
      </Stack>
    </Stack>
  );
}

export default TaskCallout;
