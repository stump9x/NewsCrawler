import {
  Box,
  CircularProgress,
  Paper,
  Table,
  TableBody,
  TableCell,
  TableContainer,
  TableHead,
  TableRow,
  Typography,
} from "@mui/material";

/**
 * Shared table. Columns may set:
 * - sx / headerSx / width / maxWidth / minWidth / align / nowrap / truncate / sticky
 */
export function DataTable({ columns, rows, loading, empty = "Không có dữ liệu" }) {
  if (loading) {
    return (
      <Box sx={{ py: 6, display: "flex", justifyContent: "center" }}>
        <CircularProgress size={28} />
      </Box>
    );
  }

  if (!rows?.length) {
    return (
      <Typography color="text.secondary" sx={{ py: 4 }}>
        {empty}
      </Typography>
    );
  }

  function cellSx(col, isHeader = false) {
    const sticky = col.sticky === "right" || col.sticky === "left";
    return {
      whiteSpace:
        col.nowrap === false
          ? "normal"
          : col.nowrap || col.truncate || (isHeader && col.nowrap !== false)
            ? "nowrap"
            : "normal",
      width: col.width,
      minWidth: col.minWidth,
      maxWidth: col.maxWidth,
      overflow: col.truncate ? "hidden" : "visible",
      textOverflow: col.truncate ? "ellipsis" : "clip",
      verticalAlign: isHeader ? undefined : "top",
      wordBreak: col.nowrap === false ? "break-word" : undefined,
      ...(sticky
        ? {
            position: "sticky",
            right: col.sticky === "right" ? 0 : undefined,
            left: col.sticky === "left" ? 0 : undefined,
            zIndex: isHeader ? 3 : 2,
            bgcolor: "background.default",
            boxShadow:
              col.sticky === "right"
                ? "-6px 0 8px -6px rgba(0,0,0,0.25)"
                : "6px 0 8px -6px rgba(0,0,0,0.25)",
          }
        : {}),
      ...(isHeader ? col.headerSx : col.sx),
    };
  }

  const useFixedLayout = columns.some((col) => col.sticky || col.width);

  return (
    <TableContainer
      component={Paper}
      elevation={0}
      sx={{
        bgcolor: "transparent",
        border: "1px solid",
        borderColor: "divider",
        overflowX: "auto",
      }}
    >
      <Table
        size="small"
        sx={{
          tableLayout: useFixedLayout ? "fixed" : "auto",
          width: "100%",
        }}
      >
        <TableHead>
          <TableRow>
            {columns.map((col) => (
              <TableCell key={col.id} align={col.align} sx={cellSx(col, true)}>
                {col.label}
              </TableCell>
            ))}
          </TableRow>
        </TableHead>
        <TableBody>
          {rows.map((row, idx) => (
            <TableRow key={row.id ?? idx} hover>
              {columns.map((col) => (
                <TableCell key={col.id} align={col.align} sx={cellSx(col)}>
                  {col.render ? col.render(row) : row[col.id]}
                </TableCell>
              ))}
            </TableRow>
          ))}
        </TableBody>
      </Table>
    </TableContainer>
  );
}
