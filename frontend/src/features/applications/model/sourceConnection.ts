import type { Models } from "@/api/client";

type Connection = Required<Models["SourceConnectionInput"]>;

export function sourceConnectionForm(
  value: Partial<Models["ApplicationResponse"]> = {},
): Connection {
  return {
    entrypoint: value.entrypoint || "",
    source_subdir: value.source_subdir || ".",
    path_mappings: (value.path_mappings || []).map((m) => ({
      runtime_prefix: String(m.runtime_prefix ?? ""),
      repository_prefix: String(m.repository_prefix ?? "."),
    })),
    git_url: value.git_url || null,
    git_ref: value.git_ref || "HEAD",
    git_username: value.git_username || "",
    git_token: "",
    clear_git_token: false,
    svn_url: value.svn_url || null,
    svn_revision: value.svn_revision || "HEAD",
    svn_username: value.svn_username || "",
    svn_password: "",
    clear_svn_password: false,
    local_path: value.local_path || null,
    enabled: value.enabled ?? true,
  };
}

export function sourceConnectionPayload(
  value: Partial<Connection>,
): Models["SourceConnectionInput"] {
  const body: Models["SourceConnectionInput"] = sourceConnectionForm(value);
  body.clear_git_token = value.clear_git_token ?? false;
  body.clear_svn_password = value.clear_svn_password ?? false;
  if (value.git_token) body.git_token = value.git_token;
  else delete body.git_token;
  if (value.svn_password) body.svn_password = value.svn_password;
  else delete body.svn_password;
  return body;
}
