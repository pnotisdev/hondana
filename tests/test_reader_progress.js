'use strict';

const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

function createReader() {
  const elements = new Map();
  const storage = new Map();
  const document = {
    getElementById(id) {
      if (!elements.has(id)) {
        elements.set(id, {
          style: {}, dataset: {}, attributes: {},
          addEventListener() {},
          setAttribute(name, value) { this.attributes[name] = value; },
        });
      }
      return elements.get(id);
    },
    addEventListener() {},
  };
  const context = vm.createContext({
    document,
    window: { addEventListener() {} },
    localStorage: {
      getItem: key => storage.get(key) ?? null,
      setItem: (key, value) => storage.set(key, value),
    },
    fetch: () => new Promise(() => {}),
  });
  vm.runInContext(fs.readFileSync(path.join(__dirname, '..', 'web', 'books', 'app.js'), 'utf8'), context);
  return { context, elements, storage };
}

test('book position estimates include the current section and clamp scroll ratios', () => {
  const { context } = createReader();
  const book = { sections: [{ path: 'a' }, { path: 'b' }, { path: 'c' }, { path: 'd' }] };
  assert.equal(context.progressPercent(book, 0, null), 0);
  assert.equal(context.progressPercent(book, 1, { x: -0.5, y: 0 }), 38);
  assert.equal(context.progressPercent(book, 1, { x: 0, y: 0.75 }), 44);
  assert.equal(context.progressPercent(book, 3, { x: 2, y: 0 }), 100);
  assert.equal(context.progressPercent(book, 2, { x: NaN, y: -1 }), 50);
});

test('reader indicator and library use the same saved-position estimate', () => {
  const { context, elements, storage } = createReader();
  vm.runInContext("currentBook = {id: 'book', sections: [{path: 'a'}, {path: 'b'}, {path: 'c'}, {path: 'd'}]}; currentIndex = 1;", context);
  storage.set('book-reader-progress:book', JSON.stringify({ path: 'b', x: -0.5, y: 0 }));
  vm.runInContext("updateReaderProgress({x: -0.5, y: 0});", context);
  assert.equal(elements.get('book-progress-label').textContent, '~38%');
  assert.equal(elements.get('book-progress-fill').style.width, '38%');
  assert.equal(elements.get('book-progress').attributes['aria-valuenow'], '38');
  assert.equal(vm.runInContext('bookProgress(currentBook).percent', context), 38);
});

test('character position uses section totals and the current scroll fraction', () => {
  const { context, elements } = createReader();
  vm.runInContext(`currentBook = {sections: [{path: 'a'}, {path: 'b'}, {path: 'c'}]};
    currentIndex = 1;
    characterCounts = {sections: [{characters: 10}, {characters: 30}, {characters: 60}], total: 100};
    updateReaderProgress({x: -0.5, y: 0});`, context);
  assert.equal(elements.get('character-progress').textContent, '~25 / 100 characters');
  assert.equal(elements.get('book-progress-label').textContent, '~25%'); // by sections it would be 50%
  assert.equal(elements.get('book-progress-fill').style.width, '25%');
  vm.runInContext('updateReaderProgress({x: 0, y: 1});', context);
  assert.equal(elements.get('character-progress').textContent, '~40 / 100 characters');
  assert.equal(elements.get('book-progress-label').textContent, '~40%');
});

test('the library shows the saved character percent, or the section estimate without one', () => {
  const { context, storage } = createReader();
  vm.runInContext(`currentBook = {id: 'book', sections: [{path: 'a'}, {path: 'b'}, {path: 'c'}]};
    currentIndex = 1; frameReady = true; currentProgress = {x: -0.5, y: 0, anchor: null};
    characterCounts = {sections: [{characters: 10}, {characters: 30}, {characters: 60}], total: 100};
    saveProgress();`, context);
  assert.equal(JSON.parse(storage.get('book-reader-progress:book')).percent, 25);
  assert.equal(vm.runInContext('bookProgress(currentBook).percent', context), 25);
  vm.runInContext('characterCounts = null; saveProgress();', context); // not counted yet
  assert.equal('percent' in JSON.parse(storage.get('book-reader-progress:book')), false);
  assert.equal(vm.runInContext('bookProgress(currentBook).percent', context), 50);
  storage.set('book-reader-progress:book', JSON.stringify({ path: 'b', x: 0, y: 0, percent: 250 }));
  assert.equal(vm.runInContext('bookProgress(currentBook).percent', context), 33); // a bad saved value is ignored
});
