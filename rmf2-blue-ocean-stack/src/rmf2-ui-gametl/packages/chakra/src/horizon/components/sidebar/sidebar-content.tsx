import type { BoxProps } from '@chakra-ui/react';
import { Box } from '@chakra-ui/react';
import { useSidebarItems } from './use-sidebar';

export interface SidebarContentProps extends BoxProps {}

export function SidebarContent(props: SidebarContentProps) {
  const { children, ...rest } = props;
  const { collapsed } = useSidebarItems();
  // SIDEBAR
  // Collapsed rail: symmetric padding so icons stay centred; expanded view
  // keeps the left inset that lines labels up under the header.
  return (
    <Box mt="8px" mb="auto" ps={collapsed ? '0' : '20px'} {...rest}>
      {children}
    </Box>
  );
}

export default SidebarContent;
