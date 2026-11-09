export type CsvColumn<T> = {
  label: string;
  value: (
    row: T,
    index: number,
  ) => string | number | boolean | null | undefined;
};

function cell(value: unknown) {
  if (value == null) return '""';
  let text = String(value);
  // Keep spreadsheet applications from treating external text as a formula.
  if (
    typeof value === "string" &&
    (/^\s*[=+\-@]/u.test(text) || /^[\t\r\n]/.test(text))
  )
    text = "'" + text;
  return '"' + text.replaceAll('"', '""') + '"';
}

export function csvText<T>(columns: CsvColumn<T>[], rows: T[]) {
  return (
    "\uFEFF" +
    [
      columns.map((column) => cell(column.label)).join(","),
      ...rows.map((row, index) =>
        columns.map((column) => cell(column.value(row, index))).join(","),
      ),
    ].join("\r\n")
  );
}

export function downloadCsv<T>(
  filename: string,
  columns: CsvColumn<T>[],
  rows: T[],
) {
  const blob = new Blob([csvText(columns, rows)], {
    type: "text/csv;charset=utf-8",
  });
  return saveBlob(
    blob,
    `${filename}-${new Date().toISOString().replace(/[:.]/g, "-")}.csv`,
  );
}
import { saveBlob } from "@/platform/desktop";
