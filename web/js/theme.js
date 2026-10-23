// Loaded synchronously at the start of <body> so the page never flashes the wrong theme.
(() => {
  const darkQuery = window.matchMedia("(prefers-color-scheme: dark)");
  const themes = ["auto", "dark", "light"];
  const labels = { auto: "Auto", dark: "Dark", light: "Light" };
  let theme = "auto";
  try {
    const saved = localStorage.getItem("seat-share-theme");
    if (themes.includes(saved)) theme = saved;
  } catch (error) {
    if (!(error instanceof DOMException)) throw error;
    console.warn("Unable to load the saved theme preference.", error);
  }
  const apply = () => {
    document.body.classList.toggle("is-dark", theme === "dark" || (theme === "auto" && darkQuery.matches));
    const toggle = document.getElementById("theme-toggle");
    if (toggle) {
      const next = themes[(themes.indexOf(theme) + 1) % themes.length];
      toggle.dataset.theme = theme;
      toggle.setAttribute("aria-label", `Theme: ${labels[theme]}. Switch to ${labels[next]} theme`);
      toggle.title = theme === "auto" ? "Auto follows your system theme" : `${labels[theme]} theme`;
    }
  };
  apply();
  darkQuery.addEventListener("change", apply);
  document.addEventListener("DOMContentLoaded", () => {
    apply();
    document.getElementById("theme-toggle").addEventListener("click", () => {
      theme = themes[(themes.indexOf(theme) + 1) % themes.length];
      apply();
      try {
        localStorage.setItem("seat-share-theme", theme);
      } catch (error) {
        if (!(error instanceof DOMException)) throw error;
        console.warn("Unable to save the theme preference.", error);
      }
    });
  });
})();
