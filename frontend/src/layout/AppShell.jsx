import { useEffect, useState } from "react";
import { Link as RouterLink, Outlet, useLocation, Navigate } from "react-router-dom";
import {
  AppBar,
  Box,
  Button,
  Divider,
  Drawer,
  IconButton,
  List,
  ListItemButton,
  ListItemIcon,
  ListItemText,
  Stack,
  Toolbar,
  Typography,
  useMediaQuery,
} from "@mui/material";
import { useTheme } from "@mui/material/styles";
import MenuIcon from "@mui/icons-material/Menu";
import DashboardOutlinedIcon from "@mui/icons-material/DashboardOutlined";
import CellTowerOutlinedIcon from "@mui/icons-material/CellTowerOutlined";
import TravelExploreOutlinedIcon from "@mui/icons-material/TravelExploreOutlined";
import AutoAwesomeOutlinedIcon from "@mui/icons-material/AutoAwesomeOutlined";
import MenuBookOutlinedIcon from "@mui/icons-material/MenuBookOutlined";
import RssFeedOutlinedIcon from "@mui/icons-material/RssFeedOutlined";
import ShieldOutlinedIcon from "@mui/icons-material/ShieldOutlined";
import AccountTreeOutlinedIcon from "@mui/icons-material/AccountTreeOutlined";
import ManageAccountsOutlinedIcon from "@mui/icons-material/ManageAccountsOutlined";
import PolicyOutlinedIcon from "@mui/icons-material/PolicyOutlined";
import { useAuth } from "../auth/AuthContext";
import { loadNavOpenPreference, writeNavOpenPreference } from "./navPreference";
import BriefingJobBanner from "../components/BriefingJobBanner";
import ChangePasswordDialog from "../components/ChangePasswordDialog";
import TacticalBackdrop from "../components/TacticalBackdrop";

const DRAWER_WIDTH = 232;

const NAV = [
  { to: "/", label: "Tổng quan", icon: <DashboardOutlinedIcon fontSize="small" /> },
  { to: "/feeds", label: "Trạm tin tức", icon: <CellTowerOutlinedIcon fontSize="small" /> },
  { to: "/mindmap", label: "Mindmap", icon: <AccountTreeOutlinedIcon fontSize="small" /> },
  { to: "/intelligence", label: "Báo cáo nhanh", icon: <AutoAwesomeOutlinedIcon fontSize="small" /> },
  { to: "/notebook-ai", label: "Phân tích sâu", icon: <MenuBookOutlinedIcon fontSize="small" /> },
  { to: "/trend", label: "Xu hướng", icon: <TravelExploreOutlinedIcon fontSize="small" /> },
  { to: "/sources", label: "Nguồn RSS", icon: <RssFeedOutlinedIcon fontSize="small" /> },
  {
    to: "/users",
    label: "Người dùng",
    icon: <ManageAccountsOutlinedIcon fontSize="small" />,
    superuserOnly: true,
  },
  {
    to: "/policy",
    label: "Chính sách",
    icon: <PolicyOutlinedIcon fontSize="small" />,
  },
];

export default function AppShell() {
  const { authed, username, isSuperuser, logout } = useAuth();
  const location = useLocation();
  const theme = useTheme();
  const compact = useMediaQuery(theme.breakpoints.down("md"));
  const [navOpen, setNavOpen] = useState(() => loadNavOpenPreference());
  const [passwordOpen, setPasswordOpen] = useState(false);

  useEffect(() => {
    if (compact) {
      setNavOpen(false);
    } else {
      setNavOpen(loadNavOpenPreference());
    }
  }, [compact]);

  useEffect(() => {
    if (!compact) {
      writeNavOpenPreference(navOpen);
    }
  }, [navOpen, compact]);

  if (!authed) {
    return <Navigate to="/login" replace state={{ from: location.pathname }} />;
  }

  const toggleNav = () => setNavOpen((open) => !open);
  const closeNav = () => setNavOpen(false);
  const sidebarVisible = navOpen && !compact;

  const drawer = (
    <Box sx={{ display: "flex", flexDirection: "column", height: "100%" }}>
      <Box sx={{ px: 2, py: 2.25, display: "flex", alignItems: "center", gap: 1 }}>
        <ShieldOutlinedIcon sx={{ color: "primary.main" }} />
        <Typography variant="h6" sx={{ color: "primary.main", letterSpacing: "-0.02em" }}>
          NewsCrawler
        </Typography>
      </Box>
      <Divider />
      <List sx={{ px: 1, py: 1.5, flex: 1 }}>
        {NAV.filter((item) => !item.superuserOnly || isSuperuser).map((item) => {
          const selected =
            item.to === "/"
              ? location.pathname === "/"
              : location.pathname.startsWith(item.to);
          return (
            <ListItemButton
              key={item.to}
              component={RouterLink}
              to={item.to}
              selected={selected}
              onClick={() => compact && closeNav()}
              sx={{
                mb: 0.5,
                borderRadius: 1,
                "&.Mui-selected": {
                  bgcolor: "rgba(76, 154, 255, 0.12)",
                  borderLeft: "2px solid",
                  borderColor: "primary.main",
                },
              }}
            >
              <ListItemIcon sx={{ minWidth: 36, color: selected ? "primary.main" : "text.secondary" }}>
                {item.icon}
              </ListItemIcon>
              <ListItemText primary={item.label} />
            </ListItemButton>
          );
        })}
      </List>
      <Box sx={{ p: 2 }}>
        <Typography variant="body2" color="text.secondary" sx={{ mb: 1 }}>
          {username}
        </Typography>
        <Stack spacing={1}>
          <Button
            fullWidth
            variant="outlined"
            onClick={() => setPasswordOpen(true)}
          >
            Đổi mật khẩu
          </Button>
          <Button fullWidth variant="outlined" color="secondary" onClick={logout}>
            Đăng xuất
          </Button>
        </Stack>
      </Box>
    </Box>
  );

  return (
    <Box sx={{ display: "flex", minHeight: "100vh", position: "relative" }}>
      <TacticalBackdrop />
      <ChangePasswordDialog
        open={passwordOpen}
        onClose={() => setPasswordOpen(false)}
      />
      <AppBar
        position="fixed"
        elevation={0}
        sx={{
          zIndex: (t) => t.zIndex.drawer + 1,
          borderBottom: "1px solid",
          borderColor: "divider",
          bgcolor: "background.default",
        }}
      >
        <Toolbar variant="dense" sx={{ minHeight: 48 }}>
          <IconButton
            edge="start"
            color="inherit"
            aria-label={navOpen ? "Ẩn thanh điều hướng" : "Hiện thanh điều hướng"}
            onClick={toggleNav}
            sx={{ mr: 0.5 }}
          >
            <MenuIcon fontSize="small" />
          </IconButton>
        </Toolbar>
      </AppBar>

      <Drawer
        variant={compact ? "temporary" : "persistent"}
        open={navOpen}
        onClose={closeNav}
        ModalProps={{ keepMounted: true }}
        sx={{
          zIndex: 1,
          width: sidebarVisible ? DRAWER_WIDTH : 0,
          flexShrink: 0,
          transition: theme.transitions.create("width", {
            easing: theme.transitions.easing.sharp,
            duration: theme.transitions.duration.enteringScreen,
          }),
          [`& .MuiDrawer-paper`]: {
            width: DRAWER_WIDTH,
            boxSizing: "border-box",
            top: 48,
            height: "calc(100% - 48px)",
            borderRight: "1px solid",
            borderColor: "divider",
            transition: theme.transitions.create("width", {
              easing: theme.transitions.easing.sharp,
              duration: theme.transitions.duration.enteringScreen,
            }),
            ...(!compact && !navOpen
              ? {
                  width: 0,
                  overflowX: "hidden",
                  borderRight: "none",
                }
              : {}),
          },
        }}
      >
        {drawer}
      </Drawer>

      <Box
        component="main"
        sx={{
          position: "relative",
          zIndex: 1,
          flexGrow: 1,
          px: { xs: 2, md: 3 },
          py: 3,
          mt: 6,
          minWidth: 0,
          transition: theme.transitions.create(["width", "margin"], {
            easing: theme.transitions.easing.sharp,
            duration: theme.transitions.duration.enteringScreen,
          }),
          width: sidebarVisible ? { md: `calc(100% - ${DRAWER_WIDTH}px)` } : "100%",
        }}
      >
        <Outlet />
      </Box>
      <BriefingJobBanner />
    </Box>
  );
}
