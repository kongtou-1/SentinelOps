import { ref } from "vue";

export const theme = ref(
  localStorage.getItem("sentinelops.theme") === "light" ? "light" : "dark",
);
export const navigationLayout = ref(
  localStorage.getItem("sentinelops.navigation") === "side" ? "side" : "top",
);
export function applyTheme() {
  document.documentElement.classList.toggle("dark", theme.value === "dark");
  document.documentElement.dataset.theme = theme.value;
}
export function toggleTheme() {
  theme.value = theme.value === "dark" ? "light" : "dark";
  localStorage.setItem("sentinelops.theme", theme.value);
  applyTheme();
}
export function toggleNavigation() {
  navigationLayout.value = navigationLayout.value === "top" ? "side" : "top";
  localStorage.setItem("sentinelops.navigation", navigationLayout.value);
}
