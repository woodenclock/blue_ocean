import { useState } from 'react';
import { Outlet, useNavigation } from 'react-router';
import { Box, Button, Flex } from '@chakra-ui/react';
import { MdChevronLeft, MdChevronRight } from 'react-icons/md';
import { Horizon } from '@rmf2-ui/chakra';
import { RMF2IndustrialLogo } from '@/components/icons';
import { Pending } from '@/components/pending';
import { SidebarPartnerLogo } from '@/components/sidebar/sidebar-partner-logo';
import { ColorModeButton } from '@/components/ui/color-mode';
import { routes } from './admin-routes';
import { AdminAvatarMenu } from './admin-avatar-menu';

import Sidebar = Horizon.Sidebar;
// eslint-disable-next-line @typescript-eslint/no-unused-vars -- kept for the commented-out Searchbar below
import Searchbar = Horizon.Searchbar;

// Fixed-sidebar widths (xl+ only). Collapsed = icon-only rail.
const SIDEBAR_W = '300px';
const SIDEBAR_COLLAPSED_W = '90px';

// Custom Chakra theme
export function AdminLayout() {
  const logoColor = { base: 'navy.700', _dark: 'white' };

  const navigation = useNavigation();
  const isNavigating = Boolean(navigation.location);

  // Collapse the fixed sidebar to an icon rail so the content (e.g. the map)
  // gets more room. Toggle lives in the sidebar footer; xl-only.
  const [collapsed, setCollapsed] = useState(false);
  const railW = collapsed ? SIDEBAR_COLLAPSED_W : SIDEBAR_W;
  const contentW = `calc(100% - ${railW})`;

  return (
    <Box h="100vh">
      <Sidebar.Root routes={routes} collapsed={collapsed}>
        <Sidebar.Positioner w={railW} transition="width 0.2s ease">
          <Sidebar.Header px={collapsed ? '0' : undefined}>
            <RMF2IndustrialLogo collapsed={collapsed} color={logoColor} />
          </Sidebar.Header>
          <Sidebar.Content>
            <Sidebar.Items />
          </Sidebar.Content>
          <Sidebar.Footer mt="auto" mb="24px" px={4}>
            <SidebarPartnerLogo collapsed={collapsed} />
            {/* {!collapsed && <Searchbar w="100%" mb="12px" />} */}
            <Flex align="center" justify="center" gap="16px" mb="12px">
              {/* Theme icon: sits above the collapse/expand toggle */}
              <ColorModeButton color={{ base: 'gray.400', _dark: 'white' }} />
              {!collapsed && <AdminAvatarMenu />}
            </Flex>
            <Button
              w="100%"
              colorPalette="blue"
              variant="solid"
              onClick={() => setCollapsed((v) => !v)}
              aria-label={collapsed ? 'Expand sidebar' : 'Collapse sidebar'}
            >
              {collapsed ? <MdChevronRight /> : <MdChevronLeft />}
            </Button>
          </Sidebar.Footer>
        </Sidebar.Positioner>

        <Box
          float="right"
          minHeight="100vh"
          height="100%"
          overflow="auto"
          position="relative"
          maxHeight="100%"
          w={{ base: '100%', xl: contentW }}
          maxWidth={{ base: '100%', xl: contentW }}
          transition="all 0.33s cubic-bezier(0.685, 0.0473, 0.346, 1)"
          transitionDuration=".2s, .2s, .35s"
          transitionProperty="top, bottom, width"
          transitionTimingFunction="linear, linear, ease"
        >
          {/* Floating Sidebar trigger (mobile/tablet only, since the fixed
              sidebar above is hidden below the xl breakpoint) */}
          <Sidebar.Responsive
            position="fixed"
            top="16px"
            insetInlineStart="16px"
            zIndex="10"
            bg={{ base: 'white', _dark: 'navy.800' }}
            borderRadius="12px"
            boxShadow="14px 17px 40px 4px rgba(112, 144, 176, 0.08)"
          >
            <Sidebar.Header>
              <RMF2IndustrialLogo color={logoColor} />
            </Sidebar.Header>
            <Sidebar.Content>
              <Sidebar.Items />
            </Sidebar.Content>
            <Sidebar.Footer mt="auto" mb="24px" px={4}>
              <SidebarPartnerLogo />
              {/* <Searchbar w="100%" mb="12px" /> */}
              <Flex align="center" justify="center" gap="16px">
                <ColorModeButton color={{ base: 'gray.400', _dark: 'white' }} />
                <AdminAvatarMenu />
              </Flex>
            </Sidebar.Footer>
          </Sidebar.Responsive>

          <Box
            mx="auto"
            p="30px"
            minH="100vh"
            pt={{ base: '80px', xl: '30px' }}
          >
            <Outlet />
            {isNavigating && (
              <Pending.Root>
                <Pending.Overlay />
                <Pending.Spinner />
              </Pending.Root>
            )}
          </Box>
        </Box>
      </Sidebar.Root>
    </Box>
  );
}

export default AdminLayout;
