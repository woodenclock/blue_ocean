// Master-side outbound order queue, per robot. Surfaces `pending_order_count`
// (streamed live on /ws/state, also at GET /orders/queue) so a stuck/zombie
// queue — orders piling up behind a stitch point or departure blocker — is
// visible at a glance. Shown above Direct Control. Read-only.
import { Badge, Box, Flex, HStack, Stack, Text } from '@chakra-ui/react';

import type { AgvState } from '../types';
import { robotColor } from './constants';

function queueBadge(count: number) {
  if (count <= 0) {
    return <Badge colorPalette="gray">Empty</Badge>;
  }
  // A single queued order is normal (blocker/stitch wait); 2+ usually means a
  // pile-up that can't drain (e.g. failed stitch combine).
  return (
    <Badge colorPalette={count >= 2 ? 'red' : 'yellow'}>{count} queued</Badge>
  );
}

export function OrderQueuePanel({ agvs }: { agvs: AgvState[] }) {
  return (
    <Stack gap={2} align="flex-start" w="100%">
      <Text fontSize="sm" fontWeight="semibold">
        Order queue
      </Text>
      {agvs.length === 0 ? (
        <Text fontSize="sm" color="gray.500">
          No robots onboarded.
        </Text>
      ) : (
        <HStack align="stretch" gap={3} flexWrap="wrap">
          {agvs.map((a, idx) => {
            const count = a.pending_order_count ?? 0;
            return (
              <Box
                key={a.robot_id}
                borderWidth="1px"
                borderColor={count >= 2 ? 'red.400' : 'border.subtle'}
                borderRadius="md"
                px={3}
                py={2}
                minW="180px"
              >
                <Flex align="stretch" gap={2}>
                  <Box flex={1}>
                    <Badge
                      colorPalette={
                        a.connection_status === 'ONLINE' ? 'green' : 'gray'
                      }
                    >
                      {a.connection_status ?? 'UNKNOWN'}
                    </Badge>
                  </Box>
                  <Box borderLeftWidth="1px" borderColor="border.subtle" />
                  <Flex flex={1} justify="flex-end">
                    {queueBadge(count)}
                  </Flex>
                </Flex>
                <Flex justify="space-between" align="center" gap={2} mt={2}>
                  <HStack gap={2}>
                    <Box
                      w="10px"
                      h="10px"
                      borderRadius="full"
                      bg={robotColor(a, idx)}
                    />
                    <Text fontSize="sm" fontWeight="medium">
                      {a.robot_id}
                    </Text>
                  </HStack>
                </Flex>
                <HStack gap={2} mt={1}>
                  <Badge
                    size="sm"
                    colorPalette={a.order_active ? 'blue' : 'gray'}
                    variant="subtle"
                  >
                    {a.order_active ? 'order active' : 'idle'}
                  </Badge>
                  <Text fontSize="xs" color="gray.500">
                    at {a.last_node_id ?? '—'}
                  </Text>
                </HStack>
              </Box>
            );
          })}
        </HStack>
      )}
    </Stack>
  );
}
