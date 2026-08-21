import { Box, Typography } from "@mui/material";

export function PageHeader({ title, subtitle, action }) {
  return (
    <Box
      sx={{
        display: "flex",
        alignItems: { xs: "flex-start", sm: "flex-end" },
        justifyContent: "space-between",
        gap: 2,
        flexWrap: "wrap",
        mb: 3,
      }}
    >
      <Box>
        <Typography variant="h4" sx={{ color: "primary.main", mb: 0.5 }}>
          {title}
        </Typography>
        {subtitle ? (
          <Typography color="text.secondary" sx={{ maxWidth: 640 }}>
            {subtitle}
          </Typography>
        ) : null}
      </Box>
      {action}
    </Box>
  );
}
