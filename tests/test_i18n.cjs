const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const staticDir = path.join(__dirname, '../app/static');

function context(languages) {
  const sandbox = vm.createContext({ navigator: { languages, language: languages[0] } });
  for (const file of ['i18n.js', 'locales/pl.js']) {
    vm.runInContext(fs.readFileSync(path.join(staticDir, file), 'utf8'), sandbox);
  }
  return sandbox;
}

test('regional Polish and ordered browser preferences', () => {
  const ctx = context(['en-US', 'pl-PL', 'fr-FR']);
  assert.equal(vm.runInContext('TapoI18n.language', ctx), 'pl');
  assert.equal(vm.runInContext('TapoI18n.locale', ctx), 'pl-PL');
  assert.equal(vm.runInContext('TapoI18n.t("Bibliothèque")', ctx), 'Biblioteka');
  assert.equal(vm.runInContext('TapoI18n.label("mouvement")', ctx), 'ruch');
  assert.equal(vm.runInContext('TapoI18n.t("Archive en continu par segments de 10\\u00a0min.")', ctx),
    'Zapisuje nagrania w plikach po 10\u00a0min.');
});

test('French preference and unsupported-language fallback preserve source', () => {
  for (const langs of [['fr-CA', 'pl'], ['de-DE'], []]) {
    const ctx = context(langs);
    assert.equal(vm.runInContext('TapoI18n.language', ctx), 'fr');
    assert.equal(vm.runInContext('TapoI18n.t("Bibliothèque")', ctx), 'Bibliothèque');
    assert.equal(vm.runInContext('TapoI18n.t`Supprimer ${"clip.mp4"} ?`', ctx), 'Supprimer clip.mp4 ?');
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
