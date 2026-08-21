import { createTheme } from "@mui/material/styles";

export const FONT_FAMILY_UI =
  '"Inter", system-ui, -apple-system, "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif';

// Steel-blue accent on deep navy — situation-room / OSINT tech palette.
const ACCENT = "#4c9aff";
const ACCENT_RGB = "76, 154, 255";

export const cyberTheme = createTheme({
  palette: {
    mode: "dark",
    primary: { main: ACCENT, contrastText: "#050c18" },
    secondary: { main: "#9ab8e0" },
    error: { main: "#ff6b7a" },
    warning: { main: "#ffc14d" },
    info: { main: "#7eb6ff" },
    success: { main: "#46d68c" },
    background: {
      default: "#050c18",
      paper: "#0a1220",
    },
    text: {
      primary: "#f0f5fc",
      secondary: "#a8b8d0",
    },
    divider: `rgba(${ACCENT_RGB}, 0.14)`,
    action: {
      hover: "rgba(76, 154, 255, 0.08)",
      selected: "rgba(76, 154, 255, 0.12)",
    },
  },
  typography: {
    fontFamily: FONT_FAMILY_UI,
    h1: { fontWeight: 700, letterSpacing: "-0.03em" },
    h2: { fontWeight: 600, letterSpacing: "-0.02em" },
    h3: { fontWeight: 600, letterSpacing: "-0.02em" },
    h4: { fontWeight: 600 },
    h5: { fontWeight: 600 },
    h6: { fontWeight: 600 },
    body1: { fontSize: "0.95rem", fontWeight: 450, letterSpacing: "0.01em" },
    body2: { fontSize: "0.875rem", fontWeight: 450, letterSpacing: "0.01em" },
    caption: {
      fontSize: "0.8125rem",
      fontWeight: 500,
      letterSpacing: "0.015em",
      color: "#c5d0e0",
    },
    button: { textTransform: "none", fontWeight: 650 },
  },
  shape: { borderRadius: 6 },
  components: {
    MuiCssBaseline: {
      styleOverrides: {
        body: {
          backgroundColor: "#050c18",
          backgroundImage:
            `radial-gradient(ellipse 90% 55% at 12% -15%, rgba(${ACCENT_RGB},0.08), transparent),` +
            "radial-gradient(ellipse 70% 45% at 92% 0%, rgba(126,182,255,0.06), transparent)",
          WebkitFontSmoothing: "antialiased",
          MozOsxFontSmoothing: "grayscale",
          textRendering: "optimizeLegibility",
        },
      },
    },
    MuiAppBar: {
      styleOverrides: {
        root: {
          backgroundImage: "none",
          backgroundColor: "rgba(5, 12, 24, 0.92)",
          borderBottom: `1px solid rgba(${ACCENT_RGB}, 0.12)`,
          backdropFilter: "blur(10px)",
        },
      },
    },
    MuiDrawer: {
      styleOverrides: {
        paper: {
          backgroundColor: "#070b12",
          borderRight: `1px solid rgba(${ACCENT_RGB}, 0.1)`,
        },
      },
    },
    MuiPaper: {
      styleOverrides: {
        root: {
          backgroundImage: "none",
        },
      },
    },
    MuiTableCell: {
      styleOverrides: {
        head: {
          color: "#8b9bb4",
          fontSize: "0.75rem",
          fontWeight: 600,
          letterSpacing: "0.04em",
          textTransform: "uppercase",
          borderBottomColor: `rgba(${ACCENT_RGB}, 0.12)`,
        },
        body: {
          borderBottomColor: "rgba(255,255,255,0.06)",
        },
      },
    },
    MuiButton: {
      styleOverrides: {
        containedPrimary: {
          boxShadow: "none",
          "&:hover": { boxShadow: `0 0 0 1px rgba(${ACCENT_RGB},0.35)` },
        },
      },
    },
  },
});
