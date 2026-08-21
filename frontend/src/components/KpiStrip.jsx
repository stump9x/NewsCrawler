import { Box, Typography } from "@mui/material";

export function KpiStrip({ items }) {
  return (
    <Box
      sx={{
        display: "grid",
        gridTemplateColumns: {
          xs: "repeat(2, minmax(0, 1fr))",
          md: `repeat(${Math.min(items.length, 4)}, minmax(0, 1fr))`,
        },
        gap: 2,
        mb: 3,
      }}
    >
      {items.map((item) => (
        <Box
          key={item.label}
          sx={{
            px: 2,
            py: 1.75,
            borderLeft: "2px solid",
            borderColor: item.accent || "primary.main",
            bgcolor: "rgba(10, 18, 32, 0.72)",
          }}
        >
          <Typography
            variant="body2"
            color="text.secondary"
            sx={{ mb: 0.75, letterSpacing: "0.04em" }}
          >
            {item.label}
          </Typography>
          <Typography variant="h4">{item.value}</Typography>
        </Box>
      ))}
    </Box>
  );
}
