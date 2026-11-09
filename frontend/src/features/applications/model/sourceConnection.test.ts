import { describe, expect, it } from "vitest";
import {
  sourceConnectionForm,
  sourceConnectionPayload,
} from "@/features/applications/model/sourceConnection";

describe("service source connection", () => {
  it("keeps saved credentials when editing and isolates path mapping changes", () => {
    const application = {
      git_url: "https://example.invalid/project.git",
      git_ref: "stable",
      git_username: "reader",
      has_git_token: true,
      path_mappings: [{ runtime_prefix: "/app", repository_prefix: "backend" }],
    };
    const form = sourceConnectionForm(application);
    form.path_mappings[0]!.repository_prefix = "worker";
    const body = sourceConnectionPayload(form);
    expect(body.git_ref).toBe("stable");
    expect(body).not.toHaveProperty("git_token");
    expect(body).not.toHaveProperty("has_git_token");
    expect(body.clear_git_token).toBe(false);
    expect(application.path_mappings[0]!.repository_prefix).toBe("backend");
  });

  it("normalizes optional empty locations and sends only explicit credential changes", () => {
    const form = sourceConnectionForm();
    form.git_url = "";
    form.local_path = "";
    form.clear_git_token = true;
    expect(sourceConnectionPayload(form)).toMatchObject({
      git_url: null,
      local_path: null,
      clear_git_token: true,
    });
    form.git_token = "replacement-test-token";
    expect(sourceConnectionPayload(form).git_token).toBe(
      "replacement-test-token",
    );
  });

  it("preserves SVN revision and never resubmits a saved password", () => {
    const form = sourceConnectionForm({
      svn_url: "https://example.invalid/svn/project/trunk",
      svn_revision: "123",
      svn_username: "reader",
      has_svn_password: true,
    });
    const body = sourceConnectionPayload(form);
    expect(body).toMatchObject({
      svn_url: "https://example.invalid/svn/project/trunk",
      svn_revision: "123",
      svn_username: "reader",
      clear_svn_password: false,
      git_url: null,
    });
    expect(body).not.toHaveProperty("svn_password");
    expect(body).not.toHaveProperty("has_svn_password");
    form.svn_password = "replacement";
    expect(sourceConnectionPayload(form).svn_password).toBe("replacement");
    form.svn_password = "";
    form.clear_svn_password = true;
    expect(sourceConnectionPayload(form).clear_svn_password).toBe(true);
    form.svn_url = "";
    expect(sourceConnectionPayload(form).svn_url).toBeNull();
  });
});
