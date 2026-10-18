"use strict";

// French source strings are the fallback catalog. Locale files only contain
// translations; no server state, cookies or language query parameters are needed.
const TapoI18n = (() => {
  const supported = new Set(["fr", "pl"]);
  const preferences = typeof navigator === "undefined" ? [] :
    (navigator.languages?.length ? navigator.languages : [navigator.language]);
  const preference = preferences.find((tag) => supported.has(String(tag).toLowerCase().split("-")[0]));
  const language = preference ? preference.toLowerCase().split("-")[0] : "fr";
  const locale = preference || "fr-FR";
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
      if (!node.parentElement.closest("script, style")) node.textContent = t(node.textContent);
    }
    for (const element of document.querySelectorAll("[title], [aria-label], [placeholder]")) {
      for (const attribute of ["title", "aria-label", "placeholder"]) {
        if (element.hasAttribute(attribute)) element.setAttribute(attribute, t(element.getAttribute(attribute)));
      }
    }
  }

  return { language, locale, t, label, message, applyDocument,
    register: (name, translations) => catalogs.set(name, translations) };
})();
