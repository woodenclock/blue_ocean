import { Center, Spinner, Box } from '@chakra-ui/react';
import { lazy, Suspense } from 'react';

const LifEditorScene = lazy(() => import('./lif-editor-scene'));

export function LifEditor() {
  return (
    <Box h={{ base: 'auto', lg: 'calc(100vh - 120px)' }} minH="600px">
      <Suspense
        fallback={
          <Center h="full">
            <Spinner />
          </Center>
        }
      >
        <LifEditorScene />
      </Suspense>
    </Box>
  );
}

export default LifEditor;
