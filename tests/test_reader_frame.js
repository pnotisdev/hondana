'use strict';

const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

function createFrame() {
  const handlers = { window: {}, document: {} };
  const sent = [];
  const parent = { postMessage: message => sent.push(message) };
  const text = { nodeType: 3, length: 20 };
  const body = { childNodes: [text], contains: node => node === body || node === text };
  text.parentNode = body;
  const root = {
    scrollWidth: 2000, clientWidth: 1000, scrollHeight: 1000, clientHeight: 500,
    scrollLeft: 0, scrollTop: 0,
    scrollBy(x, y) { this.scrollLeft += x; this.scrollTop += y; },
  };
  let style;
  const doc = {
    body, documentElement: root, scrollingElement: root,
    head: { append: element => { style = element; } },
    createElement: () => ({ textContent: '' }),
    addEventListener: (name, handler) => { handlers.document[name] = handler; },
    caretPositionFromPoint: () => ({ offsetNode: text, offset: 4 }),
    createRange: () => ({
      setStart(node, offset) { assert.equal(node, text); assert.equal(offset, 4); },
      collapse() {},
      getBoundingClientRect: () => ({ left: 700, top: 300 }),
    }),
    getSelection: () => ({
      isCollapsed: false, rangeCount: 1, toString: () => '日本語',
      getRangeAt: () => ({ startContainer: text, startOffset: 4 }),
    }),
  };
  const window = { addEventListener: (name, handler) => { handlers.window[name] = handler; } };
  const context = {
    document: doc, window, parent, location: { href: 'http://localhost/book' },
    innerWidth: 1000, innerHeight: 500, Node: { TEXT_NODE: 3 },
    getComputedStyle: () => ({ writingMode: body.writingMode || 'vertical-rl', direction: body.direction || 'ltr' }),
  };
  vm.runInNewContext(fs.readFileSync(path.join(__dirname, '..', 'web', 'books', 'frame.js'), 'utf8'), context);
  return { handlers, sent, parent, root, body, doc, context, style: () => style };
}

test('settings restore the saved position and apply Japanese typography', () => {
  const frame = createFrame();
  frame.handlers.window.message({
    source: frame.parent, data: {
      type: 'book-reader-restore', x: -0.5, y: 0.25,
      settings: { direction: 'horizontal', font: 'mincho', theme: 'paper', size: 24, spacing: 2, width: 'narrow' },
    },
  });
  assert.equal(frame.root.scrollLeft, -500);
  assert.equal(frame.root.scrollTop, 125);
  assert.match(frame.style().textContent, /writing-mode: horizontal-tb/);
  assert.match(frame.style().textContent, /font-size: 24px/);
  assert.match(frame.style().textContent, /Yu Mincho/);
  assert.match(frame.style().textContent, /#f8f4ea/);
  assert.match(frame.style().textContent, /max-height: 85vh/);
  assert.equal(frame.sent.at(-2).type, 'rendered');
  assert.equal(frame.sent.at(-1).type, 'progress');
  assert.deepEqual(Array.from(frame.sent.at(-1).anchor.path), [0]);
});

test('layout changes and viewport resizing preserve a text anchor', () => {
  const frame = createFrame();
  frame.handlers.window.message({
    source: frame.parent, data: { type: 'book-reader-settings', settings: { direction: 'vertical' } },
  });
  assert.match(frame.style().textContent, /writing-mode: vertical-rl/);
  assert.equal(frame.root.scrollLeft, 200);
  assert.equal(frame.root.scrollTop, 125);
  frame.handlers.window.resize();
  assert.equal(frame.root.scrollLeft, 400);
  assert.equal(frame.root.scrollTop, 250);
});

test('dark reading theme uses the neutral library palette', () => {
  const frame = createFrame();
  frame.handlers.window.message({
    source: frame.parent, data: { type: 'book-reader-settings', settings: { theme: 'dark' } },
  });
  assert.match(frame.style().textContent, /background: #131313/);
  assert.match(frame.style().textContent, /color: #ededed/);
});

test('selected text is reported with its passage anchor', () => {
  const frame = createFrame();
  frame.handlers.document.selectionchange();
  const selection = frame.sent.at(-1).selection;
  assert.equal(selection.text, '日本語');
  assert.deepEqual(Array.from(selection.anchor.path), [0]);
  assert.equal(selection.anchor.offset, 4);
});

test('a missing passage reports a warning and falls back to scroll position', () => {
  const frame = createFrame();
  frame.handlers.window.message({
    source: frame.parent, data: {
      type: 'book-reader-restore', x: -0.5, y: 0.25,
      anchor: { path: [99], offset: 0 }, preferAnchor: true,
    },
  });
  assert.equal(frame.root.scrollLeft, -500);
  assert.equal(frame.root.scrollTop, 125);
  assert.ok(frame.sent.some(message => message.type === 'warning'));
});

test('vertical mouse-wheel scrolling moves the page', () => {
  const frame = createFrame();
  frame.root.scrollHeight = frame.root.clientHeight + 7;
  let prevented = false;
  frame.handlers.document.wheel({
    ctrlKey: false, shiftKey: false, deltaY: 100, deltaX: 0,
    preventDefault: () => { prevented = true; },
  });

  assert.equal(prevented, true);
  assert.equal(frame.root.scrollLeft, -100);
});

test('vertical left-to-right text turns and scrolls in the positive direction', () => {
  const frame = createFrame();
  frame.body.writingMode = 'vertical-lr';
  frame.body.direction = 'rtl';
  frame.handlers.window.message({ source: frame.parent, data: { type: 'book-reader-page', direction: 1 } });
  assert.equal(frame.root.scrollLeft, 900);
  frame.handlers.document.wheel({
    ctrlKey: false, shiftKey: false, deltaY: 100, deltaX: 0,
    preventDefault() {},
  });
  assert.equal(frame.root.scrollLeft, 1000);
});

test('page controls move through vertical Japanese text and report section edges', () => {
  const frame = createFrame();
  const sendPage = direction => frame.handlers.window.message({
    source: frame.parent, data: { type: 'book-reader-page', direction },
  });
  sendPage(1);
  assert.equal(frame.root.scrollLeft, -900);
  assert.equal(frame.sent.at(-1).page.canPrevious, true);
  assert.equal(frame.sent.at(-1).page.canNext, true);
  sendPage(-1);
  assert.equal(frame.root.scrollLeft, 0);
  assert.equal(frame.sent.at(-1).page.canPrevious, false);
  frame.root.scrollLeft = -1000;
  frame.handlers.document.scroll();
  assert.equal(frame.sent.at(-1).page.canNext, false);
  sendPage(0);
  assert.equal(frame.root.scrollLeft, -1000);
});

test('arrow keys follow the reading direction and report section edges', () => {
  const frame = createFrame();
  const press = (key, extra = {}) => frame.handlers.document.keydown({ key, target: { tagName: 'BODY' }, preventDefault() {}, ...extra });
  press('ArrowLeft');
  assert.equal(frame.root.scrollLeft, -900);
  press('ArrowLeft', { shiftKey: true });
  assert.equal(frame.root.scrollLeft, -900);
  press('ArrowRight');
  assert.equal(frame.root.scrollLeft, 0);
  press('ArrowRight');
  assert.deepEqual({ type: frame.sent.at(-1).type, direction: frame.sent.at(-1).direction }, { type: 'edge', direction: -1 });
  frame.handlers.window.message({ source: frame.parent, data: { type: 'book-reader-key', key: ' ' } });
  assert.equal(frame.root.scrollLeft, -900);
  assert.equal(frame.sent.at(-1).page.rtl, true);
});

test('arriving from the next section opens at the end, and furigana can be hidden', () => {
  const frame = createFrame();
  frame.handlers.window.message({
    source: frame.parent, data: { type: 'book-reader-restore', end: true, settings: { furigana: 'hidden' } },
  });
  assert.equal(frame.root.scrollLeft, -1000);
  assert.match(frame.style().textContent, /rt \{ visibility: hidden/);
  assert.match(frame.style().textContent, /user-select: none/);
});

test('page controls and keyboard move vertically for horizontal text', () => {
  const frame = createFrame();
  frame.body.writingMode = 'horizontal-tb';
  frame.root.scrollWidth = frame.root.clientWidth;
  frame.handlers.window.message({ source: frame.parent, data: { type: 'book-reader-page', direction: 1 } });
  assert.equal(frame.root.scrollTop, 450);
  let prevented = false;
  frame.handlers.document.keydown({
    key: 'PageUp', target: { tagName: 'BODY' },
    preventDefault: () => { prevented = true; },
  });
  assert.equal(prevented, true);
  assert.equal(frame.root.scrollTop, 0);
  frame.handlers.document.keydown({
    key: 'PageDown', target: { tagName: 'TEXTAREA' },
    preventDefault: () => assert.fail('editing must not turn pages'),
  });
  assert.equal(frame.root.scrollTop, 0);
});

test('words are coloured by Anki status across text nodes, skipping furigana', async () => {
  const frame = createFrame();
  const rect = { top: 0, left: 0, bottom: 10, right: 10 };
  const paragraph = { getBoundingClientRect: () => rect };
  paragraph.closest = selector => selector.startsWith('rt') ? null : paragraph;
  const text = data => ({ nodeType: 3, data, length: data.length, parentElement: paragraph });
  const ruby = { nodeType: 3, data: 'み', parentElement: { closest: () => ({}) } }; // inside <rt>
  const nodes = [text('音で'), text('満'), ruby, text('ちている。'), { nodeType: 1, localName: 'br', parentElement: paragraph }, text('猫')];
  const highlights = new Map();
  Object.assign(frame.context, {
    NodeFilter: { SHOW_ELEMENT: 1, SHOW_TEXT: 4 }, Highlight: Set, CSS: { highlights },
    crypto: { getRandomValues: array => array },
  });
  frame.context.Node.TEXT_NODE = 3;
  const walk = [...nodes];
  frame.doc.createTreeWalker = () => ({ nextNode: () => walk.shift() ?? null });
  frame.handlers.window.message({
    source: frame.parent, data: { type: 'book-reader-settings', settings: { anki: 'on' } },
  });
  assert.match(frame.style().textContent, /::highlight\(anki-3\)/);
  const request = frame.sent.at(-1);
  assert.equal(request.type, 'words-request');
  assert.deepEqual(Array.from(request.texts), ['音で満ちている。', '猫']); // furigana left out; the line break splits
  frame.doc.createRange = () => {
    const range = { collapse() {}, getBoundingClientRect: () => ({ left: 0, top: 0 }) };
    range.setStart = (node, offset) => { range.start = [nodes.indexOf(node), offset]; };
    range.setEnd = (node, offset) => { range.end = [nodes.indexOf(node), offset]; };
    return range;
  };
  frame.handlers.window.message({
    source: frame.parent, data: {
      type: 'book-reader-server-result', requestId: request.requestId,
      results: [[['音', 3, '音'], ['で'], ['満ち', 2, '満ちる'], ['て'], ['いる', 3, '居る'], ['。']], [['猫', 3, '猫']]],
    },
  });
  await new Promise(resolve => setImmediate(resolve));
  const ranges = name => Array.from(highlights.get(name), range => [range.start, range.end]);
  assert.deepEqual(ranges('anki-3'), [[[0, 0], [0, 1]], [[3, 2], [3, 4]], [[5, 0], [5, 1]]]);
  assert.deepEqual(ranges('anki-2'), [[[1, 0], [3, 1]]]); // 満ち starts in one text node and ends in the next
  assert.equal(frame.sent.at(-1).type, 'words-stats');
  assert.deepEqual(Array.from(frame.sent.at(-1).counts), [0, 0, 1, 3]);
  assert.deepEqual(Array.from(frame.sent.at(-1).unique), [0, 0, 1, 3]);
  // After the word lists are read again (a card was just added), the same text is asked for again.
  walk.push(...nodes);
  frame.handlers.window.message({ source: frame.parent, data: { type: 'book-reader-recolour' } });
  assert.equal(frame.sent.at(-1).type, 'words-request');
  assert.deepEqual(Array.from(frame.sent.at(-1).texts), ['音で満ちている。', '猫']);
  frame.handlers.window.message({
    source: frame.parent, data: { type: 'book-reader-settings', settings: { anki: 'off' } },
  });
  assert.equal(highlights.size, 0);
  assert.equal(frame.sent.at(-1).counts, null);
});
