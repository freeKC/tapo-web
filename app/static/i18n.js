"use strict";

// French source strings are the reference text. Locale files only contain
// translations. The language comes from ?lang=xx, then the choice made in the
// header picker (kept in localStorage), then the browser preferences, and
// English when none of them is supported. Nothing is sent to the server.
const TapoI18n = (() => {
  const names = { en: "English", fr: "Français", de: "Deutsch", es: "Español", pl: "Polski" };
  const regions = { en: "en-GB", fr: "fr-FR", de: "de-DE", es: "es-ES", pl: "pl-PL" };
  const storageKey = "tapo-web.lang";
  const base = (tag) => String(tag || "").toLowerCase().split("-")[0];
  const supported = (tag) => Object.hasOwn(names, base(tag));
  const preferences = typeof navigator === "undefined" ? [] :
    (navigator.languages?.length ? navigator.languages : [navigator.language]).filter(Boolean);

  function chosen() {
    try {
      const fromUrl = new URLSearchParams(location.search).get("lang");
      if (supported(fromUrl)) return base(fromUrl);
    } catch (e) { /* no location outside a browser */ }
    try {
      const stored = localStorage.getItem(storageKey);
      if (supported(stored)) return base(stored);
    } catch (e) { /* storage blocked or missing */ }
    return null;
  }

  const preference = preferences.find(supported);
  const language = chosen() || (preference ? base(preference) : "en");
  // Keep the browser's region for dates when it matches the language.
  const locale = preferences.find((tag) => base(tag) === language) || regions[language];
  const catalogs = new Map();
  const catalog = () => catalogs.get(language) || { ui: {}, messages: {} };

  function t(source, ...values) {
    const template = Array.isArray(source) && Object.hasOwn(source, "raw");
    if (!template) return catalog().ui[source] ?? source;
    const key = source.raw.map((part, i) => part + (i < values.length ? `{{${i}}}` : "")).join("");
    const translated = catalog().ui[key];
    if (translated == null) return source.reduce((text, part, i) => text + part + (i < values.length ? values[i] : ""), "");
    return translated.replace(/\{\{(\d+)\}\}/g, (_, i) => String(values[Number(i)] ?? ""));
  }

  function label(value) {
    return catalog().messages[value] ?? value;
  }

  function message(value) {
    if (typeof value !== "string") return value;
    const messages = catalog().messages;
    if (Object.hasOwn(messages, value)) return messages[value];
    // Translate known API error fragments; preserve technical details verbatim.
    const keys = Object.keys(messages).filter((key) => key.length > 10).sort((a, b) => b.length - a.length);
    if (!keys.length) return value;
    const escaped = keys.map((key) => key.replace(/[.*+?^${}()|[\]\\]/g, "\\$&"));
    return value.replace(new RegExp(escaped.join("|"), "g"), (key) => messages[key]);
  }

  function applyDocument() {
    document.documentElement.lang = language;
    const walker = document.createTreeWalker(document.documentElement, NodeFilter.SHOW_TEXT);
    const nodes = [];
    while (walker.nextNode()) nodes.push(walker.currentNode);
    for (const node of nodes) {
      if (node.parentElement.closest("script, style")) continue;
      const raw = node.textContent, core = raw.trim();
      let out = t(raw);
      // the HTML may wrap a sentence in line breaks: look it up without them
      if (out === raw && core && core !== raw) out = raw.replace(core, t(core));
      if (out !== raw) node.textContent = out;
    }
    for (const element of document.querySelectorAll("[title], [aria-label], [placeholder]")) {
      for (const attribute of ["title", "aria-label", "placeholder"]) {
        if (element.hasAttribute(attribute)) element.setAttribute(attribute, t(element.getAttribute(attribute)));
      }
    }
  }

  // Remember the choice and reload: the page is translated once, at startup.
  // Without storage the choice rides along in the URL instead.
  function choose(lang) {
    if (!supported(lang)) return;
    const url = new URL(location.href);
    url.searchParams.delete("lang");
    let stored = false;
    try {
      localStorage.setItem(storageKey, lang);
      stored = localStorage.getItem(storageKey) === lang;
    } catch (e) { /* storage blocked */ }
    if (!stored) url.searchParams.set("lang", lang);
    location.assign(url.href);
  }

  function mountPicker(select) {
    if (!select) return;
    for (const [code, name] of Object.entries(names)) {
      const option = document.createElement("option");
      option.value = code;
      option.textContent = name;
      option.selected = code === language;
      select.append(option);
    }
    select.addEventListener("change", () => choose(select.value));
  }

  return { language, locale, names, t, label, message, applyDocument, mountPicker, choose,
    register: (name, translations) => catalogs.set(name, translations) };
})();
