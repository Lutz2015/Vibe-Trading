(() => {
  const root = document.documentElement;
  let savedTheme = null;

  try {
    savedTheme = window.localStorage.getItem("qa-theme");
  } catch {
    // Storage can be unavailable in restricted iframes and WebViews.
  }

  // Desktop restarts change the 127.0.0.1 port (and thus origin), which
  // isolates localStorage. Cookies ignore the port — use them as fallback.
  if (!savedTheme) {
    const match = document.cookie.match(/(?:^|;\s*)qa-theme=([^;]+)/);
    if (match) savedTheme = decodeURIComponent(match[1]);
  }

  let prefersDark = false;
  if (savedTheme !== "dark" && savedTheme !== "light" && typeof window.matchMedia === "function") {
    try {
      prefersDark = window.matchMedia("(prefers-color-scheme: dark)").matches;
    } catch {
      // Fall back to light when the media-query API is present but unusable.
    }
  }

  const dark = savedTheme === "dark" || (savedTheme !== "light" && prefersDark);
  root.classList.toggle("dark", dark);
  root.style.colorScheme = dark ? "dark" : "light";
})();
