// Must load before ../i18n so the detector reads English instead of the
// product default (zh-CN). ESM hoists imports; this module runs first.
try {
  window.localStorage.setItem("i18nextLng", "en");
  document.cookie = "i18nextLng=en;path=/";
} catch {
  // jsdom storage may be unavailable in some configs
}

export {};
