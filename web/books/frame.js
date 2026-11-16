(() => {
  const root = () => document.scrollingElement || document.documentElement;
  const send = (type, extra = {}) => parent.postMessage({ source: 'book-reader-frame', type, href: location.href, ...extra }, '*');
  const fonts = {
    publisher: '',
    mincho: '"Yu Mincho", "YuMincho", "Hiragino Mincho ProN", "Noto Serif CJK JP", "Noto Serif JP", "MS PMincho", serif',
    gothic: '"Yu Gothic", "YuGothic", "Hiragino Kaku Gothic ProN", "Noto Sans CJK JP", "Noto Sans JP", Meiryo, sans-serif',
  };
  const themes = {
    original: null,
    paper: { bg: '#f8f4ea', text: '#28241f', link: '#315f7b' },
    light: { bg: '#ffffff', text: '#202020', link: '#215c89' },
    dark: { bg: '#131313', text: '#ededed', link: '#9ac6db' },
  };
  let style;
  let lastPosition = null;
  const generated = new Map();
  const serverRequests = new Map();
  const BLOCKS = 'p, h1, h2, h3, h4, h5, h6, li, dt, dd, td, th, blockquote, figcaption, div, section, article, body';
  const JAPANESE = /[぀-ヿ㐀-鿿]/;
  const HIRAGANA_WORD = /^[぀-ゟー]+$/;
  const WORD_COLORS = ['235, 60, 60', '70, 140, 255', '255, 200, 0', '60, 185, 75']; // not in Anki, new, learning, known
  const wordPieces = new Map(); // block text -> pieces from the server, kept while this section is open
  let wordsOn = false;
  let wordsRun = 0;
  let wordLayers = null; // highlight name -> Highlight
  let wordStats;
  let furiganaMode = 'original';
  let furiganaGeneration = 0;
  // Untagged kanji otherwise fall back to Chinese glyph forms on non-Japanese systems.
  if (!document.documentElement.lang) document.documentElement.lang = 'ja';

  function nodeAnchor(node, offset) {
    if (!node || !document.body.contains(node)) return null;
    let wrapper = node.nodeType === Node.TEXT_NODE ? node.parentNode : node;
    while (wrapper && wrapper !== document.body && !generated.has(wrapper)) wrapper = wrapper.parentNode;
    if (generated.has(wrapper)) {
      const range = document.createRange();
      range.setStart(wrapper, 0);
      range.setEnd(node, offset);
      const fragment = range.cloneContents();
      fragment.querySelectorAll('rt, rp').forEach(element => element.remove());
      offset = fragment.textContent.length;
      node = wrapper;
    }
    const path = [];
    while (node !== document.body) {
      const parentNode = node.parentNode;
      if (!parentNode) return null;
      path.unshift(Array.prototype.indexOf.call(parentNode.childNodes, node));
      node = parentNode;
    }
    return { path, offset };
  }

  function anchorAtViewport() {
    const points = [[0.5, 0.35], [0.75, 0.2], [0.25, 0.2], [0.5, 0.7]];
    for (const [x, y] of points) {
      const px = Math.max(1, Math.floor(innerWidth * x));
      const py = Math.max(1, Math.floor(innerHeight * y));
      const position = document.caretPositionFromPoint?.(px, py);
      if (position && position.offsetNode.nodeType === Node.TEXT_NODE) {
        return nodeAnchor(position.offsetNode, position.offset);
      }
      const range = document.caretRangeFromPoint?.(px, py);
      if (range && range.startContainer.nodeType === Node.TEXT_NODE) {
        return nodeAnchor(range.startContainer, range.startOffset);
      }
    }
    return null;
  }

  function rangeForAnchor(anchor) {
    if (!anchor || !Array.isArray(anchor.path) || anchor.path.length > 256 ||
        !Number.isInteger(anchor.offset) || anchor.offset < 0) return null;
    let node = document.body;
    for (const index of anchor.path) {
      if (!Number.isInteger(index) || index < 0 || !node.childNodes[index]) return null;
      node = node.childNodes[index];
    }
    const original = generated.get(node);
    if (original) {
      if (anchor.offset > original.length) return null;
      const walker = document.createTreeWalker(node, NodeFilter.SHOW_TEXT);
      let remaining = anchor.offset;
      let text;
      let lastText = null;
      while ((text = walker.nextNode())) {
        if (text.parentElement.closest('rt, rp')) continue;
        lastText = text;
        if (remaining <= text.length) {
          const range = document.createRange();
          range.setStart(text, remaining);
          range.collapse(true);
          return range;
        }
        remaining -= text.length;
      }
      if (!lastText) return null;
      const range = document.createRange();
      range.setStart(lastText, lastText.length);
      range.collapse(true);
      return range;
    }
    const limit = node.nodeType === Node.TEXT_NODE ? node.length : node.childNodes.length;
    if (anchor.offset > limit) return null;
    const range = document.createRange();
    range.setStart(node, anchor.offset);
    range.collapse(true);
    return range;
  }

  function restore(location) {
    const el = root();
    if (location.fragment) {
      const target = document.getElementById(location.fragment);
      if (target) target.scrollIntoView();
    } else if (location.end) {
      const state = pageState();
      if (state.horizontal) el.scrollLeft = (state.reverse ? -1 : 1) * state.max;
      else el.scrollTop = state.max;
    } else {
      const range = location.preferAnchor ? rangeForAnchor(location.anchor) : null;
      if (range) {
        const rect = range.getBoundingClientRect();
        el.scrollBy(rect.left - innerWidth * 0.5, rect.top - innerHeight * 0.35);
      } else {
        if (location.preferAnchor && location.anchor) {
          send('warning', { message: 'The exact passage could not be found; using the saved scroll position.' });
        }
        el.scrollLeft = (location.x || 0) * Math.max(0, el.scrollWidth - el.clientWidth);
        el.scrollTop = (location.y || 0) * Math.max(0, el.scrollHeight - el.clientHeight);
      }
    }
  }

  function applySettings(settings) {
    if (!settings) return;
    if (!style) {
      style = document.createElement('style');
      document.head.append(style);
    }
    const size = Math.min(36, Math.max(14, Number(settings.size) || 22));
    const spacing = Math.min(2.6, Math.max(1.2, Number(settings.spacing) || 1.9));
    const font = fonts[settings.font] ?? fonts.mincho;
    const theme = themes[settings.theme] ?? null;
    const width = { narrow: '38rem', comfortable: '50rem', wide: '66rem' }[settings.width] || '50rem';
    const direction = settings.direction;
    let css = `html { font-size: ${size}px !important; }
      body { font-size: 1rem !important; line-height: ${spacing} !important; }
      p, li, blockquote { line-height: ${spacing} !important; }
      img, svg { max-width: 100%; max-height: 85vh !important; object-fit: contain; }
      rt { user-select: none !important; }`; // keeps readings out of copied text and note excerpts
    if (settings.furigana === 'hidden') {
      css += `rt { visibility: hidden !important; } ruby:hover rt { visibility: visible !important; }`;
    }
    if (settings.anki === 'on') {
      css += WORD_COLORS.map((rgb, status) => `::highlight(anki-${status}) { background-color: rgba(${rgb}, .4); }
        ::highlight(anki-${status}-alt) { background-color: rgba(${rgb}, .2); }`).join('') +
        `::highlight(anki-kana) { text-decoration: underline dotted rgba(${WORD_COLORS[0]}, .9); }`;
    }
    if (font) css += `body, body *:not(rt):not(rp) { font-family: ${font} !important; }`;
    if (theme) {
      css += `html, body { background: ${theme.bg} !important; color: ${theme.text} !important; }
        body * { color: inherit !important; background-color: transparent !important; }
        a { color: ${theme.link} !important; }`;
    }
    if (direction === 'horizontal') {
      css += `html, body, body * { writing-mode: horizontal-tb !important; }
        html { height: auto !important; overflow: auto !important; }
        body { box-sizing: border-box !important; width: min(100%, ${width}) !important;
          max-width: ${width} !important; height: auto !important; min-height: 100vh !important;
          margin: 0 auto !important; padding: 2rem clamp(1rem, 4vw, 3rem) !important; }`;
    } else if (direction === 'vertical') {
      css += `html, body, body * { writing-mode: vertical-rl !important; }
        html { height: 100% !important; overflow: auto !important; }
        body { box-sizing: border-box !important; width: auto !important; max-width: none !important;
          height: 100% !important; max-height: 100% !important; margin: 0 !important;
          padding: clamp(1rem, 4vh, 2.5rem) !important; }`;
    }
    style.textContent = css;
    updateFurigana(settings.furigana === 'generated' ? 'generated' : 'original');
  }

  // The parent page asks the server: this frame's content policy allows no requests of its own.
  function askServer(type, texts) {
    const requestId = Array.from(crypto.getRandomValues(new Uint32Array(4))).join('-');
    return new Promise((resolve, reject) => {
      serverRequests.set(requestId, { resolve, reject });
      send(type, { requestId, texts });
    });
  }

  function updateFurigana(mode) {
    if (mode === furiganaMode) return;
    furiganaMode = mode;
    const generation = ++furiganaGeneration;
    for (const [wrapper, original] of generated) {
      if (document.body.contains(wrapper)) wrapper.replaceWith(original);
    }
    if (generated.size && wordsOn) colourWords(); // the coloured text nodes were just swapped
    generated.clear();
    if (mode === 'generated') generateFurigana(generation);
    else send('furigana-status', { message: '' });
  }

  // ---------- Anki colours ----------
  // Words are painted with the CSS Custom Highlight API, so the book's DOM stays as it is:
  // reading position, selection, notes and Yomitan are not affected.
  function textBlocks() {
    const open = new Map(); // block element -> the block its text is being added to
    const blocks = [];      // { text, nodes: [[text node, offset in text], ...] }, furigana left out
    const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_ELEMENT | NodeFilter.SHOW_TEXT);
    let node;
    while ((node = walker.nextNode())) {
      if (node.nodeType !== Node.TEXT_NODE) {
        if (node.localName === 'br') open.delete(node.parentElement.closest(BLOCKS)); // a line break ends the sentence
        continue;
      }
      if (node.parentElement.closest('rt, rp, script, style, svg, math')) continue;
      const element = node.parentElement.closest(BLOCKS);
      let block = open.get(element);
      if (!block) {
        block = { text: '', nodes: [] };
        open.set(element, block);
        blocks.push(block);
      }
      block.nodes.push([node, block.text.length]);
      block.text += node.data;
    }
    // shortcut: a paragraph over 20,000 characters (the server's limit per text) stays uncoloured; split it if a book has one
    return blocks.filter(block => JAPANESE.test(block.text) && block.text.length <= 20000);
  }

  function validPieces(pieces, text) {
    return Array.isArray(pieces) && pieces.every(piece => Array.isArray(piece) && typeof piece[0] === 'string' &&
      (piece.length === 1 || (Number.isInteger(piece[1]) && piece[1] >= 0 && piece[1] < WORD_COLORS.length &&
        typeof piece[2] === 'string'))) && pieces.map(piece => piece[0]).join('') === text;
  }

  function paintWords(blocks) {
    for (const { text, nodes } of blocks) {
      const pieces = wordPieces.get(text);
      if (!pieces) continue;
      let offset = 0;
      let first = 0;     // index of the text node holding the current offset
      let previous = -1; // status of the word just before, when it has a background colour
      let alt = false;
      for (const [piece, status, word] of pieces) {
        const end = offset + piece.length;
        if (status === undefined) {
          previous = -1;
        } else {
          // Hiragana words missing from Anki get an underline: red on every unknown もう would bury the page.
          const kana = status === 0 && HIRAGANA_WORD.test(piece);
          // A lighter shade after a word of the same colour, so neighbours don't read as one block.
          alt = !kana && status === previous && !alt;
          previous = kana ? -1 : status;
          while (nodes[first + 1] && nodes[first + 1][1] <= offset) first++;
          let last = first;
          while (nodes[last + 1] && nodes[last + 1][1] < end) last++;
          const range = document.createRange();
          range.setStart(nodes[first][0], offset - nodes[first][1]);
          range.setEnd(nodes[last][0], end - nodes[last][1]);
          wordLayers.get(kana ? 'anki-kana' : `anki-${status}${alt ? '-alt' : ''}`).add(range);
          wordStats.counts[status]++;
          wordStats.words[status].add(word);
        }
        offset = end;
      }
    }
    send('words-stats', { counts: wordStats.counts, unique: wordStats.words.map(words => words.size) });
  }

  async function colourWords() {
    const run = ++wordsRun;
    for (const name of wordLayers ? wordLayers.keys() : []) CSS.highlights.delete(name);
    wordLayers = null;
    if (!wordsOn) {
      send('words-stats', { counts: null });
      return;
    }
    if (typeof Highlight !== 'function' || typeof CSS === 'undefined' || !CSS.highlights) {
      send('warning', { message: 'This browser cannot colour words: it does not support CSS highlights.' });
      return;
    }
    wordLayers = new Map();
    for (const name of ['anki-kana', ...WORD_COLORS.flatMap((rgb, status) => [`anki-${status}`, `anki-${status}-alt`])]) {
      wordLayers.set(name, new Highlight());
      CSS.highlights.set(name, wordLayers.get(name));
    }
    wordStats = { counts: WORD_COLORS.map(() => 0), words: WORD_COLORS.map(() => new Set()) };
    const blocks = textBlocks();
    paintWords(blocks); // what the server already answered, such as after furigana was generated
    let todo = blocks.filter(block => !wordPieces.has(block.text));
    // The page on screen first, then the rest of the section.
    const visible = todo.findIndex(block => {
      const rect = block.nodes[0][0].parentElement.getBoundingClientRect();
      return rect.bottom > 0 && rect.right > 0 && rect.top < innerHeight && rect.left < innerWidth;
    });
    if (visible > 0) todo = [...todo.slice(visible), ...todo.slice(0, visible)];
    try {
      for (let i = 0; i < todo.length;) {
        const batch = [];
        let length = 0;
        // Small batches, so the first colours appear quickly.
        do {
          length += todo[i].text.length;
          batch.push(todo[i++]);
        } while (i < todo.length && batch.length < 100 && length + todo[i].text.length <= 8000);
        const results = await askServer('words-request', batch.map(block => block.text));
        if (!Array.isArray(results) || results.length !== batch.length ||
            !batch.every((block, index) => validPieces(results[index], block.text))) {
          throw new Error('Invalid word status response.');
        }
        batch.forEach((block, index) => wordPieces.set(block.text, results[index]));
        if (run !== wordsRun) return;
        paintWords(batch);
      }
    } catch (error) {
      if (run === wordsRun) send('warning', { message: error.message || 'Could not colour words.' });
    }
  }

  function updateWords(settings) {
    const on = !!settings && settings.anki === 'on';
    if (on === wordsOn) return;
    wordsOn = on;
    colourWords();
  }

  async function generateFurigana(generation) {
    send('furigana-status', { message: 'Generating furigana…' });
    try {
      const nodes = [];
      const tasks = [];
      const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
      let node;
      while ((node = walker.nextNode())) {
        if (!/[\u3400-\u9fff\uf900-\ufaff\u{20000}-\u{2fa1f}々〆]/u.test(node.data) ||
            node.parentElement.closest('ruby, rt, rp, script, style, textarea, input, select, option, svg, math')) continue;
        const index = nodes.length;
        nodes.push({ node, parts: [] });
        const chars = Array.from(node.data);
        for (let i = 0; i < chars.length; i += 10000) {
          tasks.push({ index, text: chars.slice(i, i + 10000).join('') });
        }
      }
      for (let i = 0; i < tasks.length;) {
        const batch = [];
        let length = 0;
        while (i < tasks.length && batch.length < 100 && length + Array.from(tasks[i].text).length <= 50000) {
          const task = tasks[i++];
          batch.push(task);
          length += Array.from(task.text).length;
        }
        const results = await askServer('furigana-request', batch.map(task => task.text));
        if (generation !== furiganaGeneration) return;
        if (!Array.isArray(results) || results.length !== batch.length) throw new Error('Invalid furigana response.');
        for (let j = 0; j < batch.length; j++) {
          const parts = results[j];
          if (!Array.isArray(parts) || parts.some(part => !part || typeof part.text !== 'string' ||
              (part.reading !== undefined && typeof part.reading !== 'string')) ||
              parts.map(part => part.text).join('') !== batch[j].text) {
            throw new Error('Invalid furigana response.');
          }
          nodes[batch[j].index].parts.push(...parts);
        }
      }
      if (generation !== furiganaGeneration) return;
      const anchor = anchorAtViewport();
      for (const entry of nodes) {
        if (!document.body.contains(entry.node) || !entry.parts.some(part => part.reading)) continue;
        const wrapper = document.createElement('span');
        for (const part of entry.parts) {
          if (part.reading) {
            const ruby = document.createElement('ruby');
            ruby.append(document.createTextNode(part.text));
            const reading = document.createElement('rt');
            reading.textContent = part.reading;
            ruby.append(reading);
            wrapper.append(ruby);
          } else {
            wrapper.append(document.createTextNode(part.text));
          }
        }
        entry.node.replaceWith(wrapper);
        generated.set(wrapper, entry.node);
      }
      if (anchor) restore({ anchor, preferAnchor: true });
      report();
      send('furigana-status', { message: '' });
      if (wordsOn) colourWords(); // the coloured text nodes were just swapped
    } catch (error) {
      if (generation === furiganaGeneration) send('warning', { message: error.message || 'Could not generate furigana.' });
    }
  }

  function pageState() {
    const el = root();
    const { writingMode: mode, direction } = getComputedStyle(document.body);
    const maxX = Math.max(0, el.scrollWidth - el.clientWidth);
    const maxY = Math.max(0, el.scrollHeight - el.clientHeight);
    const horizontal = maxX > 1 && (mode.startsWith('vertical') || maxY <= 1);
    const rtl = mode === 'vertical-rl' || (!mode.startsWith('vertical') && direction === 'rtl');
    const reverse = horizontal && rtl;
    const max = horizontal ? maxX : maxY;
    const position = horizontal ? el.scrollLeft * (reverse ? -1 : 1) : el.scrollTop;
    return { horizontal, reverse, rtl, max, canPrevious: position > 1, canNext: position < max - 1 };
  }

  function keyDirection(event) {
    if (event.key === 'PageDown' || event.key === 'PageUp') return event.key === 'PageDown' ? 1 : -1;
    if (event.key === ' ') return event.shiftKey ? -1 : 1;
    // Shift+arrows extend a text selection, so they never turn pages.
    if (event.shiftKey || (event.key !== 'ArrowLeft' && event.key !== 'ArrowRight')) return 0;
    return (event.key === 'ArrowLeft') === pageState().rtl ? 1 : -1;
  }

  function turnPage(direction) {
    const el = root();
    const state = pageState();
    if (direction > 0 ? !state.canNext : !state.canPrevious) {
      send('edge', { direction });
      return;
    }
    if (state.horizontal) {
      el.scrollBy(direction * (state.reverse ? -1 : 1) * el.clientWidth * 0.9, 0);
    } else {
      el.scrollBy(0, direction * el.clientHeight * 0.9);
    }
    report();
  }

  const report = () => {
    const el = root();
    const maxX = Math.max(0, el.scrollWidth - el.clientWidth);
    const maxY = Math.max(0, el.scrollHeight - el.clientHeight);
    lastPosition = { x: maxX ? el.scrollLeft / maxX : 0, y: maxY ? el.scrollTop / maxY : 0, anchor: anchorAtViewport() };
    const state = pageState();
    send('progress', { ...lastPosition, page: { canPrevious: state.canPrevious, canNext: state.canNext, rtl: state.rtl } });
  };

  window.addEventListener('message', event => {
    if (event.source !== parent || !event.data) return;
    if (event.data.type === 'book-reader-restore') {
      applySettings(event.data.settings);
      restore(event.data);
      send('rendered');
      report();
      updateWords(event.data.settings);
    } else if (event.data.type === 'book-reader-settings') {
      const anchor = anchorAtViewport();
      const el = root();
      const position = {
        x: el.scrollLeft / Math.max(1, el.scrollWidth - el.clientWidth),
        y: el.scrollTop / Math.max(1, el.scrollHeight - el.clientHeight),
        anchor, preferAnchor: !!anchor,
      };
      applySettings(event.data.settings);
      restore(position);
      report();
      updateWords(event.data.settings);
    } else if (event.data.type === 'book-reader-recolour') {
      wordPieces.clear(); // the word lists changed: ask the server again
      if (wordsOn) colourWords();
    } else if (event.data.type === 'book-reader-page' &&
               (event.data.direction === -1 || event.data.direction === 1)) {
      turnPage(event.data.direction);
    } else if (event.data.type === 'book-reader-key') {
      const direction = keyDirection(event.data);
      if (direction) turnPage(direction);
    } else if (event.data.type === 'book-reader-server-result' && typeof event.data.requestId === 'string') {
      const request = serverRequests.get(event.data.requestId);
      if (!request) return;
      serverRequests.delete(event.data.requestId);
      if (event.data.error) request.reject(new Error(event.data.error));
      else request.resolve(event.data.results);
    }
  });
  document.addEventListener('scroll', report, true);
  window.addEventListener('resize', () => {
    if (!lastPosition) return;
    restore({ ...lastPosition, preferAnchor: !!lastPosition.anchor });
    report();
  });
  document.addEventListener('selectionchange', () => {
    const selection = document.getSelection();
    if (!selection || selection.isCollapsed || !selection.rangeCount) {
      send('selection', { selection: null });
      return;
    }
    const range = selection.getRangeAt(0);
    const text = selection.toString().trim().slice(0, 500);
    send('selection', { selection: text ? { text, anchor: nodeAnchor(range.startContainer, range.startOffset) } : null });
  });
  document.addEventListener('wheel', event => {
    const el = root();
    const state = pageState();
    if (!event.ctrlKey && !event.shiftKey && Math.abs(event.deltaY) > Math.abs(event.deltaX) &&
        state.horizontal && state.max > 1) {
      event.preventDefault();
      el.scrollLeft += (state.reverse ? -1 : 1) * event.deltaY;
    }
  }, { passive: false });
  document.addEventListener('keydown', event => {
    if (event.defaultPrevented || event.ctrlKey || event.metaKey || event.altKey ||
        event.target.isContentEditable || /^(INPUT|TEXTAREA|SELECT)$/.test(event.target.tagName)) return;
    const direction = keyDirection(event);
    if (direction) {
      event.preventDefault();
      turnPage(direction);
    }
  });
  document.addEventListener('click', event => {
    const link = event.target.closest('a[href]');
    if (!link) return;
    event.preventDefault();
    send('link', { target: new URL(link.getAttribute('href'), location.href).href });
  });
  window.addEventListener('load', () => send('ready'));
})();
