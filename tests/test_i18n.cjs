const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const staticDir = path.join(__dirname, '../app/static');

const locales = ['en', 'de', 'es', 'pl'];

function context(languages, { search = '', stored = null } = {}) {
  const store = new Map(stored ? [['tapo-web.lang', stored]] : []);
  const sandbox = vm.createContext({
    navigator: { languages, language: languages[0] },
    location: { search, href: `http://localhost:8088/${search}`, assign(url) { sandbox.assigned = url; } },
    localStorage: { getItem: (k) => store.get(k) ?? null, setItem: (k, v) => store.set(k, String(v)) },
    URL, URLSearchParams,
  });
  sandbox.store = store;
  for (const file of ['i18n.js', ...locales.map((l) => `locales/${l}.js`)]) {
    vm.runInContext(fs.readFileSync(path.join(staticDir, file), 'utf8'), sandbox);
  }
  return sandbox;
}

function catalog(lang) {
  const src = fs.readFileSync(path.join(staticDir, `locales/${lang}.js`), 'utf8');
  return JSON.parse(src.slice(src.indexOf('{'), src.lastIndexOf('}') + 1));
}

test('download speed uses the selected locale and reports idle only during transfer', () => {
  const source = fs.readFileSync(path.join(staticDir, 'app.js'), 'utf8');
  const duration = source.slice(source.indexOf('function fmtDur('), source.indexOf('// ---------- tabs'));
  const status = source.slice(source.indexOf('function jobText('), source.indexOf('function refreshClip('));
  for (const [language, speed, idle] of [
    ['pl', '1,25 MB/s', 'Brak nowych danych od 6 s'],
    ['en', '1.25 MB/s', 'No new data for 6 s'],
  ]) {
    const ctx = context([language]);
    vm.runInContext('const t = TapoI18n.t;\n' + duration + '\n' + status, ctx);
    const text = vm.runInContext('jobText({state:"streaming",progress:5,total:60,bytes_per_second:1250000,idle_seconds:6})', ctx);
    assert.ok(text.includes(speed), text);
    assert.ok(text.includes(idle), text);
    assert.ok(!vm.runInContext('jobText({state:"finalizing",idle_seconds:6})', ctx).includes('MB/s'));
  }
});

test('regional Polish and ordered browser preferences', () => {
  const ctx = context(['ja-JP', 'pl-PL', 'fr-FR']);
  assert.equal(vm.runInContext('TapoI18n.language', ctx), 'pl');
  assert.equal(vm.runInContext('TapoI18n.locale', ctx), 'pl-PL');
  assert.equal(vm.runInContext('TapoI18n.t("Bibliothèque")', ctx), 'Biblioteka');
  assert.equal(vm.runInContext('TapoI18n.label("mouvement")', ctx), 'ruch');
  assert.equal(vm.runInContext('TapoI18n.t("Archive en continu par segments de 10\\u00a0min.")', ctx),
    'Zapisuje nagrania w plikach po 10\u00a0min.');
});

test('French preference keeps the source text', () => {
  const ctx = context(['fr-CA', 'pl']);
  assert.equal(vm.runInContext('TapoI18n.language', ctx), 'fr');
  assert.equal(vm.runInContext('TapoI18n.locale', ctx), 'fr-CA');
  assert.equal(vm.runInContext('TapoI18n.t("Bibliothèque")', ctx), 'Bibliothèque');
  assert.equal(vm.runInContext('TapoI18n.t`Supprimer ${"clip.mp4"} ?`', ctx), 'Supprimer clip.mp4 ?');
});

test('unsupported languages fall back to English', () => {
  for (const langs of [['ja-JP'], ['nl-NL', 'it-IT'], []]) {
    const ctx = context(langs);
    assert.equal(vm.runInContext('TapoI18n.language', ctx), 'en');
    assert.equal(vm.runInContext('TapoI18n.locale', ctx), 'en-GB');
    assert.equal(vm.runInContext('TapoI18n.t("Bibliothèque")', ctx), 'Library');
    assert.equal(vm.runInContext('TapoI18n.t`Supprimer ${"clip.mp4"} ?`', ctx), 'Delete clip.mp4?');
  }
});

test('German and Spanish catalogs', () => {
  const de = context(['de-AT']);
  assert.equal(vm.runInContext('TapoI18n.locale', de), 'de-AT');
  assert.equal(vm.runInContext('TapoI18n.t("Carte SD")', de), 'SD-Karte');
  assert.equal(vm.runInContext('TapoI18n.label("renard")', de), 'Fuchs');
  const es = context(['es-ES']);
  assert.equal(vm.runInContext('TapoI18n.t`Récupération depuis la caméra… ${"0:10"} / ${"1:06"}`', es),
    'Trayendo de la cámara… 0:10 / 1:06');
});

test('picked language wins over the browser, the URL wins over both', () => {
  assert.equal(vm.runInContext('TapoI18n.language', context(['fr-FR'], { stored: 'de' })), 'de');
  assert.equal(vm.runInContext('TapoI18n.language', context(['fr-FR'], { stored: 'xx' })), 'fr');
  assert.equal(vm.runInContext('TapoI18n.language', context(['fr-FR'], { stored: 'de', search: '?lang=es' })), 'es');
  assert.equal(vm.runInContext('TapoI18n.locale', context(['fr-FR'], { stored: 'pl' })), 'pl-PL');
});

test('choosing a language stores it and reloads', () => {
  const ctx = context(['fr-FR'], { search: '?lang=pl' });
  vm.runInContext('TapoI18n.choose("en")', ctx);
  assert.equal(ctx.store.get('tapo-web.lang'), 'en');
  assert.equal(ctx.assigned, 'http://localhost:8088/');
  vm.runInContext('TapoI18n.choose("klingon")', ctx);
  assert.equal(ctx.store.get('tapo-web.lang'), 'en');
});

test('every catalog covers the same strings and keeps the placeholders', () => {
  const reference = catalog('pl');
  const holders = (text) => new Set(text.match(/\{\{\d+\}\}/g) || []);
  for (const lang of locales) {
    const other = catalog(lang);
    for (const section of ['ui', 'messages']) {
      assert.deepEqual(Object.keys(other[section]).sort(), Object.keys(reference[section]).sort(), `${lang} ${section}`);
      for (const [key, value] of Object.entries(other[section])) {
        for (const h of holders(value)) assert.ok(holders(key).has(h), `${lang}: ${key}`);
        assert.ok(!/[\u2013\u2014]/.test(value), `${lang}: dash in ${value}`);
      }
    }
  }
});

test('templates preserve interpolated values literally', () => {
  const ctx = context(['pl']);
  assert.equal(vm.runInContext('TapoI18n.t`Supprimer ${"$& {{0}}.mp4"} ?`', ctx), 'Usunąć $& {{0}}.mp4?');
  assert.equal(vm.runInContext('TapoI18n.t`Untranslated ${42}`', ctx), 'Untranslated 42');
});

test('API messages and unknown technical details', () => {
  const ctx = context(['pl']);
  assert.equal(vm.runInContext('TapoI18n.message("API de contrôle : -40404 Caméra hors ligne.")', ctx),
    'API sterowania: -40404 Brak połączenia z kamerą.');
  assert.equal(vm.runInContext('TapoI18n.message("unknown-code=123")', ctx), 'unknown-code=123');
});

test('document text, title, accessibility labels and placeholders', () => {
  const ctx = context(['pl']);
  vm.runInContext(`
    const textNodes = ["Bibliothèque", "Carte SD"].map(textContent => ({
      textContent, parentElement: { closest: () => null }
    }));
    const attributes = { title: "Identifiants de la caméra", placeholder: "laisser vide pour conserver" };
    const field = { hasAttribute: key => key in attributes,
      getAttribute: key => attributes[key], setAttribute: (key, val) => attributes[key] = val };
    let index = -1;
    const document = { documentElement: {}, querySelectorAll: () => [field],
      createTreeWalker: () => ({ nextNode() { return ++index < textNodes.length; },
        get currentNode() { return textNodes[index]; } }) };
    const NodeFilter = { SHOW_TEXT: 4 };
    TapoI18n.applyDocument();
  `, ctx);
  assert.equal(vm.runInContext('document.documentElement.lang', ctx), 'pl');
  assert.equal(vm.runInContext('textNodes[0].textContent', ctx), 'Biblioteka');
  assert.equal(vm.runInContext('attributes.title', ctx), 'Dane logowania kamery');
  assert.equal(vm.runInContext('attributes.placeholder', ctx), 'pozostaw puste, aby zachować obecne');
});

test('every string of the interface has a translation', () => {
  const ui = catalog('en').ui;
  const js = fs.readFileSync(path.join(staticDir, 'app.js'), 'utf8');
  const keys = new Set();
  for (const m of js.matchAll(/\bt\("((?:[^"\\]|\\.)*)"\)/g)) keys.add(JSON.parse(`"${m[1]}"`));
  for (const m of js.matchAll(/\bt`((?:[^`\\]|\\.)*)`/g)) {
    let i = 0;
    keys.add(m[1].replace(/\$\{[^}]*\}/g, () => `{{${i++}}}`));
  }
  const html = fs.readFileSync(path.join(staticDir, 'index.html'), 'utf8')
    .replace(/<script[\s\S]*?<\/script>/g, '').replace(/<style[\s\S]*?<\/style>/g, '');
  for (const m of html.matchAll(/>([^<>]+)</g)) {
    const text = m[1].replace(/&nbsp;/g, ' ').trim();
    if (text) keys.add(text);
  }
  for (const m of html.matchAll(/\s(?:title|aria-label|placeholder)="([^"]*)"/g)) keys.add(m[1]);
  // names, units and numbers read the same in every language
  const same = /^[^a-zà-ÿ]*$|^Tapo ?Web$|^\d+ h$|^cameras\.json$|^\.env$/;
  const norm = (k) => k.trim().replace(/\s+/g, ' ');
  const known = new Set(Object.keys(ui).map(norm));
  const missing = [...keys].filter((k) => !same.test(k) && !(k in ui) && !known.has(norm(k)));
  assert.deepEqual(missing, []);
});
