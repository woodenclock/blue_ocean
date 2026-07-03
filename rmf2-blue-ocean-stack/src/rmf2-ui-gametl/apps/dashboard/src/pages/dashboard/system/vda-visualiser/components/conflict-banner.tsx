// Shown when the scheduler returns HTTP 409 — surfaces the server detail and
// offers Clear conflict (cancel + retry) or Dismiss.
import { Button, HStack, Stack, Text } from '@chakra-ui/react';

interface ConflictBannerProps {
  detail: string;
  robotId: string;
  goalNode?: string;
  clearing: boolean;
  onClear: () => void;
  onDismiss: () => void;
  variant?: 'standalone' | 'inline';
}

export function ConflictBanner({
  detail,
  robotId,
  goalNode,
  clearing,
  onClear,
  onDismiss,
  variant = 'standalone',
}: ConflictBannerProps) {
  const content = (
    <>
      <Stack gap={1} flex="1" minW={0}>
        <Text fontSize="sm" fontWeight="semibold" color="orange.800">
          Navigation conflict — {robotId}
        </Text>
        <Text fontSize="sm" color="orange.900">
          {detail}
        </Text>
        {goalNode && (
          <Text fontSize="xs" color="orange.700">
            Pending goal: {goalNode}
          </Text>
        )}
      </Stack>
      <HStack gap={2} flexShrink={0} flexWrap="wrap">
        <Button
          size="sm"
          colorPalette="orange"
          loading={clearing}
          onClick={onClear}
        >
          Clear conflict
        </Button>
        <Button size="sm" variant="outline" onClick={onDismiss}>
          Dismiss
        </Button>
      </HStack>
    </>
  );

  if (variant === 'inline') {
    return (
      <Stack
        gap={3}
        px={3}
        py={2}
        w="100%"
        maxW="100%"
        borderWidth="1px"
        borderColor="orange.300"
        bg="orange.50"
        borderRadius="md"
        borderLeftWidth="4px"
        borderLeftColor="orange.500"
        align="stretch"
      >
        {content}
      </Stack>
    );
  }

  return (
    <HStack
      gap={3}
      px={4}
      py={3}
      borderWidth="1px"
      borderColor="orange.300"
      bg="orange.50"
      borderRadius="lg"
      borderLeftWidth="4px"
      borderLeftColor="orange.500"
      align="flex-start"
      flexWrap="wrap"
    >
      {content}
    </HStack>
  );
}

export default ConflictBanner;
