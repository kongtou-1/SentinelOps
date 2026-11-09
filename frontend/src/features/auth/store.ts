import { defineStore } from "pinia";
import { ref } from "vue";
import { api, saveTokens, tokens, type Models } from "@/api/client";

export const useAuth = defineStore("auth", () => {
  const user = ref<Models["MeResponse"] | null>(null);
  const can = (permission: string) =>
    permission === "resources:read"
      ? !!user.value?.permissions.some((p) =>
          [
            "servers:read",
            "containers:read",
            "applications:read",
            "service_monitors:read",
            "service_databases:read",
          ].includes(p),
        )
      : !!user.value?.permissions.includes(permission);
  async function load() {
    if (tokens()) user.value = await api<Models["MeResponse"]>("/me");
    return user.value;
  }
  async function login(username: string, password: string) {
    saveTokens(
      await api<Models["TokenResponse"]>("/auth/login", "POST", {
        username,
        password,
      }),
    );
    await load();
  }
  async function logout() {
    try {
      await api("/auth/logout", "POST");
    } finally {
      saveTokens(null);
      user.value = null;
    }
  }
  function clear() {
    user.value = null;
    saveTokens(null);
  }
  return { user, can, load, login, logout, clear };
});
