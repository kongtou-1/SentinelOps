import { api, query, request } from "@/api/client";

export type MarketKind = "skill" | "tool";
export interface Category {
  id: string;
  name: string;
  description: string;
  sort_order: number;
  enabled: boolean;
  count: number;
  updated_at: string;
}
export interface MarketContent {
  category_id: string;
  title: string;
  summary: string;
  tags: string[];
  description: string;
  usage: string;
  author: string;
  source_url: string;
  target_url: string;
  platform: string;
  skill_version: string;
  requirements: string;
  license: string;
  pricing: "unknown" | "free" | "freemium" | "paid" | "open_source";
  reason: string;
}
export interface Revision extends MarketContent {
  id: string;
  number: number;
  edit_version: number;
  status: "draft" | "pending" | "approved" | "rejected";
  submitted_at: string | null;
  reviewed_at: string | null;
  reviewer_id: string | null;
  review_note: string;
  updated_at: string;
}
export interface MarketAudit {
  id: string;
  action: string;
  user_name: string;
  created_at: string;
  detail: { note?: string; number?: number; name?: string };
}
export interface Entry {
  id: string;
  kind: MarketKind;
  owner_id: string;
  owner_name: string;
  listed: boolean;
  published_at: string | null;
  published_revision_id: string | null;
  category_name: string;
  revision: Revision;
  revisions?: Revision[];
  audit?: MarketAudit[];
}
export const statusNames: Record<string, string> = {
  draft: "草稿",
  pending: "待审核",
  approved: "审核通过",
  rejected: "已退回",
};
export const pricingNames = {
  unknown: "费用未提供",
  free: "免费",
  freemium: "部分免费",
  paid: "付费",
  open_source: "开源",
};
export const auditNames: Record<string, string> = {
  draft_created: "创建草稿",
  draft_saved: "保存草稿",
  submitted: "提交审核",
  withdrawn: "撤回审核",
  approved: "审核通过",
  rejected: "退回修改",
  unpublished: "内容下架",
  category_created: "新增分类",
  category_updated: "修改分类",
  category_deleted: "删除分类",
};
export function emptyContent(): MarketContent {
  return {
    category_id: "",
    title: "",
    summary: "",
    tags: [],
    description: "",
    usage: "",
    author: "",
    source_url: "",
    target_url: "",
    platform: "",
    skill_version: "",
    requirements: "",
    license: "",
    pricing: "unknown",
    reason: "",
  };
}
export const getCategories = (kind?: MarketKind) =>
  api<Category[]>("/market/categories" + query({ kind }));
export const listEntries = (filters: Record<string, unknown>) =>
  request<Entry[]>("/market/entries" + query(filters));
export const getEntry = (id: string, preview = false) =>
  api<Entry>(`/market/entries/${id}${preview ? "/workbench" : ""}`);
