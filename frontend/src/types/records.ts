import type { CsvColumn } from "@/utils/csv";
export type Row = Record<string, any>;
export type Field = {
  key: string;
  label: string;
  type?:
    | "number"
    | "switch"
    | "select"
    | "textarea"
    | "password"
    | "tags"
    | "json";
  required?: boolean;
  options?: { label: string; value: string }[];
  source?: string;
  hint?: string;
  min?: number;
  max?: number;
  default?: unknown;
  nullable?: boolean;
};
export type ListConfig = {
  title: string;
  subtitle: string;
  read: string;
  manage?: string;
  columns: {
    key: string;
    label: string;
    width?: number;
    status?: boolean;
    format?: (r: Row) => string;
  }[];
  fields?: Field[];
  detail?: boolean;
  search?: boolean;
  states?: string[];
  dateFilter?: boolean;
  exportColumns?: CsvColumn<Row>[];
};
