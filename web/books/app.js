'use strict';

const $ = id => document.getElementById(id);
const library = $('library');
const reader = $('reader');
const frame = $('page');
const sections = $('sections');
const storagePrefix = 'book-reader-progress:';
const notesPrefix = 'book-reader-notes:';
const settingsKey = 'book-reader-settings';
const defaultSettings = { direction: 'original', font: 'mincho', theme: 'paper', size: 22, spacing: 1.9, width: 'comfortable', furigana: 'original', anki: 'off' };
const settingsOptions = {
  direction: ['original', 'vertical', 'horizontal'], font: ['publisher', 'mincho', 'gothic'],
  theme: ['original', 'paper', 'light', 'dark'], width: ['narrow', 'comfortable', 'wide'],
  furigana: ['original', 'generated', 'hidden'], anki: ['off', 'on'],
};
// What the book frame may ask the server: message type -> [endpoint, name used in error messages].
const frameRequests = { 'furigana-request': ['/api/furigana', 'Furigana'], 'words-request': ['/api/word-status', 'Word colouring'] };
const wordLabels = ['Not in Anki', 'New', 'Learning', 'Known']; // by the status number the server sends
let ankiOffline = false;
let books = [];
let contentOrigin = '';
let libraryItems = [];
let currentBook = null;
let selectedSeries = null;
let currentIndex = 0;
let frameReady = false;
let currentProgress = null;
let pendingRestore = null;
let characterCounts = null;
let characterCountRequest = null;
let loadTimer;
let saveTimer;
let settings = { ...defaultSettings };
let currentSelection = null;
let notes = [];
let notesAvailable = true;
let editingNote = null;
let draftLocation = null;
let draftInitialText = '';
let openItem = null;
const difficulty = new Map(); // library item key -> {deckId, value, description} | null
let easiestFirst = false;
try { easiestFirst = localStorage.getItem('book-library-sort') === 'difficulty'; } catch (error) { /* storage blocked: default sort */ }

function reportStorageError(message) {
  const status = $('library-status');
  status.dataset.error = 'true';
  status.textContent = message;
  if (!reader.hidden) $('reader-status').textContent = message;
}

function validAnchor(anchor) {
  return anchor && Array.isArray(anchor.path) && anchor.path.length <= 256 &&
    anchor.path.every(value => Number.isInteger(value) && value >= 0) &&
    Number.isInteger(anchor.offset) && anchor.offset >= 0;
}

function loadSettings() {
  try {
    const saved = JSON.parse(localStorage.getItem(settingsKey));
    if (!saved || typeof saved !== 'object') return;
    for (const [key, values] of Object.entries(settingsOptions)) {
      if (values.includes(saved[key])) settings[key] = saved[key];
    }
    if (Number.isFinite(saved.size) && saved.size >= 14 && saved.size <= 36) settings.size = saved.size;
    if (Number.isFinite(saved.spacing) && saved.spacing >= 1.2 && saved.spacing <= 2.6) settings.spacing = saved.spacing;
  } catch (error) {
    reportStorageError('Could not load reading settings: ' + error.message);
  }
}

function updateSettingsControls() {
  for (const key of Object.keys(defaultSettings)) $('setting-' + key).value = String(settings[key]);
  $('size-value').textContent = settings.size + ' px';
  $('spacing-value').textContent = settings.spacing.toFixed(1) + '×';
  reader.dataset.pageTheme = settings.theme;
}

function applySettings() {
  updateSettingsControls();
  try {
    localStorage.setItem(settingsKey, JSON.stringify(settings));
  } catch (error) {
    $('reader-status').textContent = 'Could not save reading settings: ' + error.message;
  }
  if (frameReady) frame.contentWindow.postMessage({ type: 'book-reader-settings', settings }, contentOrigin);
}

function showDrawer(name) {
  const active = name && !$('reader-sidebar').hidden && !$(`${name}-panel`).hidden ? null : name;
  $('reader-sidebar').hidden = !active;
  for (const panel of ['settings', 'notes']) {
    $(`${panel}-panel`).hidden = panel !== active;
    $(`toggle-${panel}`).setAttribute('aria-expanded', String(panel === active));
  }
  if (active) $(`${active}-close`).focus();
}

function loadNotes() {
  notes = [];
  notesAvailable = true;
  try {
    const stored = JSON.parse(localStorage.getItem(notesPrefix + currentBook.id));
    if (stored === null) return;
    if (!Array.isArray(stored) || stored.some(note => !note || typeof note.id !== 'string' ||
      typeof note.text !== 'string' || !note.location || typeof note.location.path !== 'string')) {
      throw new Error('Stored notes have an invalid format');
    }
    notes = stored;
  } catch (error) {
    notesAvailable = false;
    $('reader-status').textContent = 'Could not load notes; saving is disabled: ' + error.message;
  }
}

function writeNotes(updated) {
  if (!notesAvailable) {
    $('reader-status').textContent = 'Notes cannot be saved because the stored notes could not be loaded.';
    return false;
  }
  try {
    localStorage.setItem(notesPrefix + currentBook.id, JSON.stringify(updated));
    notes = updated;
    return true;
  } catch (error) {
    $('reader-status').textContent = 'Could not save notes: ' + error.message;
    return false;
  }
}

function noteSection(note) {
  const section = currentBook.sections.find(item => item.path === note.location?.path);
  return section ? section.title : 'Section unavailable';
}

function renderNotes() {
  const list = $('notes-list');
  list.replaceChildren();
  $('notes-count').textContent = notes.length ? String(notes.length) : '';
  if (!notes.length) {
    const empty = document.createElement('p');
    empty.className = 'notes-empty';
    empty.textContent = notesAvailable ? 'No notes yet. Add one to remember a word, grammar point, or thought.' : 'Your notes could not be loaded.';
    list.append(empty);
    return;
  }
  for (const note of [...notes].reverse()) {
    const card = document.createElement('article');
    card.className = 'note-card';
    const location = document.createElement('p');
    location.className = 'note-location';
    location.textContent = noteSection(note);
    card.append(location);
    if (note.quote) {
      const quote = document.createElement('blockquote');
      quote.className = 'note-excerpt';
      quote.textContent = note.quote;
      card.append(quote);
    }
    const body = document.createElement('div');
    body.className = 'note-body';
    body.textContent = note.text;
    const actions = document.createElement('div');
    actions.className = 'note-card-actions';
    const jump = document.createElement('button');
    jump.className = 'secondary';
    jump.textContent = 'Go to passage';
    jump.addEventListener('click', () => {
      const index = currentBook.sections.findIndex(section => section.path === note.location?.path);
      if (index < 0) {
        $('reader-status').textContent = 'This note’s section is no longer available.';
        return;
      }
      showDrawer(null);
      showSection(index, { ...note.location, preferAnchor: true });
    });
    const edit = document.createElement('button');
    edit.className = 'secondary';
    edit.textContent = 'Edit';
    edit.addEventListener('click', () => startNote(note));
    const remove = document.createElement('button');
    remove.className = 'secondary';
    remove.textContent = 'Delete';
    remove.addEventListener('click', () => {
      if (confirm('Delete this note?') && writeNotes(notes.filter(item => item.id !== note.id))) {
        if (editingNote === note.id) cancelNote();
        renderNotes();
      }
    });
    actions.append(jump, edit, remove);
    card.append(body, actions);
    list.append(card);
  }
}

function startNote(note = null) {
  if (!frameReady && !note) {
    $('reader-status').textContent = 'Wait for the section to finish loading before adding a note.';
    return;
  }
  if (!$('note-form').hidden && $('note-text').value !== draftInitialText &&
      !confirm('Discard the note you are editing?')) return;
  editingNote = note?.id || null;
  draftLocation = note?.location || {
    path: currentBook.sections[currentIndex].path,
    x: currentProgress?.x || 0,
    y: currentProgress?.y || 0,
    anchor: currentSelection?.anchor || currentProgress?.anchor || null,
  };
  $('note-location').textContent = note ? noteSection(note) : currentBook.sections[currentIndex].title;
  $('note-quote').textContent = note ? (note.quote || '') : (currentSelection?.text || '');
  $('note-quote').hidden = !$('note-quote').textContent;
  $('note-text').value = note?.text || '';
  draftInitialText = $('note-text').value;
  $('note-form').hidden = false;
  $('note-text').focus();
}

function cancelNote() {
  $('note-form').hidden = true;
  $('note-text').value = '';
  editingNote = null;
  draftLocation = null;
  draftInitialText = '';
}

function updateWordLegend(stats) {
  const legend = $('word-legend');
  const total = stats ? stats.counts.reduce((sum, count) => sum + count, 0) : 0;
  legend.hidden = !total;
  legend.replaceChildren(...(total ? [3, 2, 1, 0] : []).map(status => {
    const item = document.createElement('span');
    item.dataset.status = String(status);
    item.textContent = `${wordLabels[status]} ${Math.round(100 * stats.counts[status] / total)}%`;
    item.title = `${stats.unique[status].toLocaleString()} different words in this section`;
    return item;
  }));
  if (total && ankiOffline) legend.append('Anki offline: using the last saved list');
}

function getProgress(book) {
  try {
    const value = JSON.parse(localStorage.getItem(storagePrefix + book.id));
    return value && typeof value === 'object' ? value : null;
  } catch (error) {
    reportStorageError('Could not read saved progress: ' + error.message);
    return null;
  }
}

function saveProgress() {
  if (!currentBook || !frameReady || !currentProgress) return;
  const progress = {
    path: currentBook.sections[currentIndex].path,
    x: currentProgress.x,
    y: currentProgress.y,
    anchor: currentProgress.anchor,
    // By characters, for the library; left out (and so estimated from sections there) until they are counted.
    percent: characterCounts ? readingPercent(currentProgress) : undefined,
    settings: JSON.stringify(settings),
    layout: 'open',
    at: Date.now(),
  };
  try {
    localStorage.setItem(storagePrefix + currentBook.id, JSON.stringify(progress));
  } catch (error) {
    $('reader-status').textContent = 'Could not save reading progress: ' + error.message;
  }
}

function resourceUrl(book, path) {
  return '/epub-content/' + encodeURIComponent(book.id) + '/' + path.split('/').map(encodeURIComponent).join('/');
}

function readerResourceUrl(book, path) {
  return contentOrigin + resourceUrl(book, path);
}

const naturalSort = new Intl.Collator(undefined, { numeric: true, sensitivity: 'base' });

function buildLibraryItems() {
  const grouped = new Map();
  const items = [];
  for (const book of books) {
    if (!book.seriesKey) {
      items.push({ title: book.title, books: [book], key: null });
      continue;
    }
    let group = grouped.get(book.seriesKey);
    if (!group) {
      group = { title: book.series, books: [], key: book.seriesKey };
      grouped.set(book.seriesKey, group);
      items.push(group);
    }
    group.books.push(book);
  }
  for (const item of items) {
    item.books.sort((a, b) => {
      if (a.volume !== null && b.volume !== null && a.volume !== b.volume) return a.volume - b.volume;
      if (a.volume !== null && b.volume === null) return -1;
      if (a.volume === null && b.volume !== null) return 1;
      return naturalSort.compare(a.title, b.title) || naturalSort.compare(a.id, b.id);
    });
  }
  return items.sort((a, b) => naturalSort.compare(a.title, b.title));
}

const itemKey = item => item.key || 'book:' + item.books[0].id;

const itemByline = item => item.key ? item.books.length + (item.books.length === 1 ? ' volume' : ' volumes') : item.books[0].author || 'Unknown author';

function libraryDescription(item) {
  const text = itemByline(item);
  const d = difficulty.get(itemKey(item));
  return d ? text + ' · Jiten ' + d.value + '/5' : text;
}

function sectionScrollFraction(progress) {
  const x = Number.isFinite(progress?.x) ? Math.abs(progress.x) : 0;
  const y = Number.isFinite(progress?.y) ? Math.max(0, progress.y) : 0;
  return Math.min(1, Math.max(x, y));
}

function progressPercent(book, index, progress) {
  return Math.round((index + sectionScrollFraction(progress)) / book.sections.length * 100);
}

function bookProgress(book) {
  const progress = getProgress(book);
  const index = progress ? book.sections.findIndex(section => section.path === progress.path) : -1;
  const saved = Number.isInteger(progress?.percent) && progress.percent >= 0 && progress.percent <= 100;
  const percent = index < 0 ? 0 : saved ? progress.percent : progressPercent(book, index, progress);
  return { started: index >= 0, percent };
}

function characterPosition(progress) {
  const before = characterCounts.sections.slice(0, currentIndex).reduce((sum, item) => sum + item.characters, 0);
  return before + Math.round(characterCounts.sections[currentIndex].characters * sectionScrollFraction(progress));
}

// By characters once they are counted, since sections differ a lot in length; by sections until then.
function readingPercent(progress) {
  if (!characterCounts || !characterCounts.total) return progressPercent(currentBook, currentIndex, progress);
  return Math.round(characterPosition(progress) / characterCounts.total * 100);
}

function updateReaderProgress(progress) {
  const percent = readingPercent(progress);
  $('book-progress-fill').style.width = percent + '%';
  $('book-progress').setAttribute('aria-valuenow', String(percent));
  $('book-progress').setAttribute('aria-valuetext', `Estimated ${percent}% through the book`);
  $('book-progress-label').textContent = `~${percent}%`;
  if (characterCounts) {
    $('character-progress').textContent = `~${characterPosition(progress).toLocaleString()} / ${characterCounts.total.toLocaleString()} characters`;
  }
}

async function loadCharacterCounts(book) {
  if (characterCountRequest) characterCountRequest.abort();
  const controller = new AbortController();
  characterCountRequest = controller;
  characterCounts = null;
  $('character-progress').textContent = 'Counting characters…';
  $('character-progress').title = 'Estimated position in kana, kanji, letters and digits; punctuation and furigana are not counted.';
  try {
    const response = await fetch('/api/book-character-count?book=' + encodeURIComponent(book.id), { signal: controller.signal });
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    const result = await response.json();
    if (!Array.isArray(result.sections) || result.sections.length !== book.sections.length ||
        !result.sections.every((section, index) => section.path === book.sections[index].path &&
          Number.isSafeInteger(section.characters) && section.characters >= 0) ||
        !Number.isSafeInteger(result.total) ||
        result.total !== result.sections.reduce((sum, section) => sum + section.characters, 0)) {
      throw new Error('Invalid character counts');
    }
    if (controller.signal.aborted || currentBook !== book) return;
    characterCounts = result;
    updateReaderProgress(currentProgress || pendingRestore);
  } catch (error) {
    if (controller.signal.aborted || currentBook !== book) return;
    $('character-progress').textContent = 'Character count unavailable';
    $('character-progress').title = 'Could not count characters: ' + error.message;
  }
}

function makeCover(book) {
  const cover = document.createElement('div');
  cover.className = 'cover';
  if (book.cover) {
    const img = document.createElement('img');
    img.src = book.cover;
    img.alt = '';
    img.loading = 'lazy';
    cover.append(img);
  } else {
    const placeholder = document.createElement('span');
    placeholder.className = 'cover-placeholder';
    placeholder.textContent = book.title;
    cover.append(placeholder);
  }
  return cover;
}

function makeCard(book, titleText, description, onClick, progress = bookProgress(book)) {
  const card = document.createElement('button');
  card.className = 'book-card';
  const cover = makeCover(book);
  if (progress.started) {
    const track = document.createElement('div');
    track.className = 'progress-track';
    const bar = document.createElement('div');
    bar.className = 'progress-bar';
    bar.style.width = progress.percent + '%';
    track.append(bar);
    cover.append(track);
  }
  const text = document.createElement('div');
  const title = document.createElement('div');
  title.className = 'card-title';
  title.textContent = titleText;
  const author = document.createElement('div');
  author.className = 'card-author';
  author.textContent = description;
  const status = document.createElement('div');
  status.className = progress.started ? 'card-progress' : 'visually-hidden';
  status.textContent = progress.started ? progress.percent + '% read' : 'Not started';
  text.append(title, author, status);
  card.append(cover, text);
  card.addEventListener('click', onClick);
  return card;
}

function closeSeries(clearSelection = true) {
  $('series-panel').classList.remove('open');
  $('series-panel').setAttribute('aria-hidden', 'true');
  $('series-panel').hidden = true;
  $('series-scrim').classList.remove('open');
  if (clearSelection) {
    const key = openItem && itemKey(openItem);
    const trigger = Array.from($('book-grid').querySelectorAll('button')).find(card => card.dataset.seriesKey === key);
    selectedSeries = null;
    if (trigger) trigger.focus();
  }
}

function fillSeriesInfo() {
  const d = difficulty.get(itemKey(openItem));
  const line = $('series-difficulty');
  if (d) {
    const link = document.createElement('a');
    link.href = 'https://jiten.moe/decks/media/' + d.deckId + '/stats';
    link.target = '_blank';
    link.rel = 'noopener';
    link.textContent = 'Jiten difficulty ' + d.value + '/5 ↗';
    line.replaceChildren(link);
  } else line.textContent = difficulty.has(itemKey(openItem)) ? 'Difficulty unknown' : 'Looking up difficulty…';
  // The EPUB's own blurb is usually Japanese; Jiten's comes from AniList in English.
  const summary = openItem.books[0].description || d?.description || '';
  $('series-summary').textContent = summary;
  $('summary-section').hidden = !summary;
}

function openSeries(item) {
  selectedSeries = item.key;
  openItem = item;
  $('series-title').textContent = item.title;
  $('series-count').textContent = itemByline(item);
  $('volumes-heading').textContent = item.key ? 'Volumes' : 'Book';
  $('series-cover').replaceChildren(makeCover(item.books[0]));
  fillSeriesInfo();
  const grid = $('volume-grid');
  grid.replaceChildren();
  for (const book of item.books) {
    const volumeLabel = book.volume !== null ? 'Volume ' + book.volume : book.title;
    grid.append(makeCard(book, volumeLabel, book.title, () => {
      closeSeries(false);
      openBook(book, true);
    }));
  }
  $('series-panel').hidden = false;
  $('series-panel').classList.add('open');
  $('series-panel').setAttribute('aria-hidden', 'false');
  $('series-scrim').classList.add('open');
  $('series-close').focus();
}

function renderLibrary() {
  const query = $('search').value.trim().toLocaleLowerCase();
  const filtered = libraryItems.filter(item => (item.title + ' ' + item.books.map(book => book.title + ' ' + book.author).join(' ')).toLocaleLowerCase().includes(query));
  // Unrated items sort last (the Jiten scale tops out at 5); ties keep name order.
  const level = item => Number(difficulty.get(itemKey(item))?.value ?? 9);
  if (easiestFirst) filtered.sort((a, b) => level(a) - level(b));
  const last = books.map(book => ({ book, at: getProgress(book)?.at || 0 })).sort((a, b) => b.at - a.at)[0];
  $('continue').hidden = !last?.at;
  if (last?.at) {
    $('continue').textContent = `Continue reading: ${last.book.title} · ${bookProgress(last.book).percent}%`;
    $('continue').onclick = () => {
      selectedSeries = last.book.seriesKey;
      openBook(last.book, true);
    };
  }
  const grid = $('book-grid');
  grid.replaceChildren();
  for (const item of filtered) {
    const first = item.books[0];
    const volumeProgress = item.key ? item.books.map(bookProgress) : null;
    const progress = volumeProgress ? {
      started: volumeProgress.some(value => value.started),
      percent: Math.round(volumeProgress.reduce((sum, value) => sum + value.percent, 0) / volumeProgress.length),
    } : bookProgress(first);
    const card = makeCard(first, item.title, libraryDescription(item), () => openSeries(item), progress);
    card.dataset.seriesKey = itemKey(item);
    grid.append(card);
  }
  if (!filtered.length && books.length && !$('library-status').dataset.error) $('library-status').textContent = 'No books match your search.';
  else if (!$('library-status').dataset.error) $('library-status').textContent = books.length ? '' : 'No EPUBs found in the book folder.';
}

async function loadDifficulty() {
  const todo = libraryItems.filter(item => !difficulty.has(itemKey(item)));
  let next = 0;
  async function worker() {
    while (next < todo.length) {
      const item = todo[next++];
      let value = null;
      try {
        const response = await fetch('/api/difficulty?type=novel&title=' + encodeURIComponent(item.title));
        const data = response.ok ? await response.json() : null;
        const level = typeof data?.difficultyRaw === 'number' ? data.difficultyRaw.toFixed(1) : data?.difficulty;
        if (Number.isInteger(data?.deckId) && level != null) {
          value = { deckId: data.deckId, value: level, description: typeof data.description === 'string' ? data.description : '' };
        }
      } catch (error) { /* no match or lookup failed: shown as unknown */ }
      difficulty.set(itemKey(item), value);
      const card = Array.from($('book-grid').children).find(element => element.dataset.seriesKey === itemKey(item));
      if (card) card.querySelector('.card-author').textContent = libraryDescription(item);
      if (openItem === item && !$('series-panel').hidden) fillSeriesInfo();
    }
  }
  await Promise.all(Array.from({ length: 3 }, worker));
  if (easiestFirst && todo.length) renderLibrary();
}

function updateSortButton() {
  $('sort').textContent = easiestFirst ? 'Sort: easiest first' : 'Sort: name';
}

function showSection(index, progress = null, fragment = '') {
  clearTimeout(saveTimer);
  if (frameReady) saveProgress();
  currentIndex = index;
  frameReady = false;
  frame.classList.add('is-loading');
  currentProgress = null;
  $('page-previous').disabled = true;
  $('page-next').disabled = true;
  currentSelection = null;
  updateWordLegend(null);
  pendingRestore = {
    x: progress?.x || 0, y: progress?.y || 0, fragment, end: !!progress?.end,
    anchor: validAnchor(progress?.anchor) ? progress.anchor : null,
    preferAnchor: !!progress?.preferAnchor || (!!progress && progress.layout !== 'open') ||
      (!!progress?.settings && progress.settings !== JSON.stringify(settings)),
  };
  sections.value = String(index);
  $('reading-progress').textContent = `${index + 1} / ${currentBook.sections.length}`;
  updateReaderProgress(progress);
  $('previous').disabled = index === 0;
  $('next').disabled = index === currentBook.sections.length - 1;
  $('reader-status').textContent = '';
  clearTimeout(loadTimer);
  loadTimer = setTimeout(() => {
    if (frame.classList.contains('is-loading')) $('reader-status').textContent = 'Could not load this section.';
  }, 10000);
  frame.src = readerResourceUrl(currentBook, currentBook.sections[index].path);
}

window.addEventListener('message', event => {
  const data = event.data;
  if (!currentBook || event.source !== frame.contentWindow || event.origin !== contentOrigin ||
      !data || data.source !== 'book-reader-frame') return;
  let url;
  try {
    url = new URL(data.href);
  } catch {
    return;
  }
  const expected = new URL(readerResourceUrl(currentBook, currentBook.sections[currentIndex].path));
  if (url.origin !== contentOrigin || url.pathname !== expected.pathname) return;
  if (data.type === 'ready') {
    frameReady = true;
    frame.contentWindow.postMessage({ type: 'book-reader-restore', ...pendingRestore, settings }, contentOrigin);
  } else if (data.type === 'rendered' && frameReady) {
    clearTimeout(loadTimer);
    frame.classList.remove('is-loading');
    if ($('reader-status').textContent === 'Could not load this section.') $('reader-status').textContent = '';
  } else if (data.type === 'progress' && frameReady && Number.isFinite(data.x) && Number.isFinite(data.y)) {
    currentProgress = { x: data.x, y: data.y, anchor: validAnchor(data.anchor) ? data.anchor : null };
    updateReaderProgress(currentProgress);
    if (data.page && typeof data.page.canPrevious === 'boolean' && typeof data.page.canNext === 'boolean') {
      // At a section edge the page buttons continue into the neighbouring section.
      $('page-previous').disabled = !data.page.canPrevious && currentIndex === 0;
      $('page-next').disabled = !data.page.canNext && currentIndex === currentBook.sections.length - 1;
      reader.dataset.rtl = String(!!data.page.rtl);
    }
    clearTimeout(saveTimer);
    saveTimer = setTimeout(saveProgress, 250);
  } else if (data.type === 'edge' && frameReady && (data.direction === 1 || data.direction === -1)) {
    const index = currentIndex + data.direction;
    if (index >= 0 && index < currentBook.sections.length) showSection(index, data.direction < 0 ? { end: true } : null);
  } else if (data.type === 'selection' && frameReady) {
    currentSelection = data.selection && typeof data.selection.text === 'string' ?
      { text: data.selection.text.slice(0, 500), anchor: validAnchor(data.selection.anchor) ? data.selection.anchor : null } : null;
  } else if (data.type === 'warning' && typeof data.message === 'string') {
    $('reader-status').textContent = data.message;
  } else if (data.type === 'furigana-status' && typeof data.message === 'string') {
    if (data.message || $('reader-status').textContent === 'Generating furigana…') {
      $('reader-status').textContent = data.message;
    }
  } else if (data.type === 'words-stats') {
    const valid = list => Array.isArray(list) && list.length === wordLabels.length &&
      list.every(value => Number.isSafeInteger(value) && value >= 0);
    updateWordLegend(valid(data.counts) && valid(data.unique) ? data : null);
  } else if (Object.hasOwn(frameRequests, data.type) && frameReady &&
             typeof data.requestId === 'string' && Array.isArray(data.texts)) {
    askServer(...frameRequests[data.type], data, frame.src);
  } else if (data.type === 'link' && typeof data.target === 'string') {
    let target;
    try {
      target = new URL(data.target);
    } catch {
      $('reader-status').textContent = 'Invalid book link.';
      return;
    }
    if (target.origin !== contentOrigin) {
      $('reader-status').textContent = 'External links are disabled in books.';
      return;
    }
    const match = currentBook.sections.findIndex(section => new URL(readerResourceUrl(currentBook, section.path)).pathname === target.pathname);
    if (match < 0) {
      $('reader-status').textContent = 'This link does not point to a readable section.';
    } else {
      showSection(match, null, decodeURIComponent(target.hash.slice(1)));
    }
  }
});

async function askServer(url, name, data, frameUrl) {
  let reply;
  try {
    const response = await fetch(url, {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ texts: data.texts }),
    });
    if (!response.ok) {
      throw new Error(response.status === 503
        ? `${name} is unavailable. Install the optional reader dependencies; see the book reader guide.`
        : `${name} failed (HTTP ${response.status}).`);
    }
    const payload = await response.json();
    if (!payload.results) throw new Error('Anki is not running, so words cannot be coloured. Start Anki, then switch Anki colours off and on.');
    if (url === '/api/word-status') ankiOffline = !!payload.ankiOffline;
    reply = { results: payload.results };
  } catch (error) {
    reply = { error: error.message };
  }
  if (frameReady && frame.src === frameUrl) {
    frame.contentWindow.postMessage({ type: 'book-reader-server-result', requestId: data.requestId, ...reply }, contentOrigin);
  }
}

function openBook(book, updateHistory) {
  if (frameReady) saveProgress();
  frameReady = false;
  currentBook = book;
  loadCharacterCounts(book);
  const progress = getProgress(book);
  const index = progress ? book.sections.findIndex(section => section.path === progress.path) : -1;
  $('reader-title').textContent = book.title;
  $('reader-author').textContent = book.author;
  sections.replaceChildren(...book.sections.map((section, i) => {
    const option = document.createElement('option');
    option.value = String(i);
    option.textContent = section.title;
    return option;
  }));
  library.hidden = true;
  reader.hidden = false;
  if (updateHistory) history.pushState(null, '', '?book=' + encodeURIComponent(book.id));
  showSection(Math.max(0, index), index >= 0 ? progress : null);
  cancelNote();
  loadNotes();
  renderNotes();
}

function showLibrary(updateHistory) {
  clearTimeout(saveTimer);
  clearTimeout(loadTimer);
  saveProgress();
  frameReady = false;
  currentBook = null;
  if (characterCountRequest) characterCountRequest.abort();
  characterCountRequest = null;
  characterCounts = null;
  currentSelection = null;
  showDrawer(null);
  cancelNote();
  frame.removeAttribute('src');
  reader.hidden = true;
  library.hidden = false;
  if (updateHistory) history.pushState(null, '', '/books/');
  renderLibrary();
  const series = libraryItems.find(item => item.key && item.key === selectedSeries);
  if (series) openSeries(series);
}

function restoreLocation() {
  const id = new URLSearchParams(location.search).get('book');
  const book = books.find(item => item.id === id);
  if (book) {
    selectedSeries = book.seriesKey;
    closeSeries(false);
    openBook(book, false);
  }
  else {
    showLibrary(false);
    if (id) $('library-status').textContent = 'Book not found in the library.';
  }
}

$('back').addEventListener('click', () => {
  if (canLeaveDraft()) showLibrary(true);
});
$('sort').addEventListener('click', () => {
  easiestFirst = !easiestFirst;
  try { localStorage.setItem('book-library-sort', easiestFirst ? 'difficulty' : 'name'); } catch (error) { /* choice lasts for this page only */ }
  updateSortButton();
  renderLibrary();
});
$('series-close').addEventListener('click', () => closeSeries());
$('series-scrim').addEventListener('click', () => closeSeries());
$('series-panel').addEventListener('keydown', event => {
  if (event.key === 'Escape') {
    closeSeries();
  } else if (event.key === 'Tab') {
    const buttons = Array.from($('series-panel').querySelectorAll('button:not(:disabled)'));
    const first = buttons[0];
    const last = buttons[buttons.length - 1];
    if (event.shiftKey && document.activeElement === first) {
      event.preventDefault();
      last.focus();
    } else if (!event.shiftKey && document.activeElement === last) {
      event.preventDefault();
      first.focus();
    }
  }
});
$('previous').addEventListener('click', () => showSection(currentIndex - 1));
$('next').addEventListener('click', () => showSection(currentIndex + 1));
$('page-previous').addEventListener('click', () => turnPage(-1));
$('page-next').addEventListener('click', () => turnPage(1));
function turnPage(direction) {
  if (frameReady) frame.contentWindow.postMessage({ type: 'book-reader-page', direction }, contentOrigin);
}
document.addEventListener('keydown', event => {
  if (reader.hidden || !$('reader-sidebar').hidden || event.defaultPrevented ||
      event.ctrlKey || event.metaKey || event.altKey || event.target.isContentEditable ||
      /^(INPUT|TEXTAREA|SELECT)$/.test(event.target.tagName)) return;
  if (event.key === ' ' && event.target.tagName === 'BUTTON') return;
  if (['PageDown', 'PageUp', ' ', 'ArrowLeft', 'ArrowRight'].includes(event.key) && frameReady) {
    event.preventDefault();
    // The frame knows the reading direction, so it decides which way each key turns.
    frame.contentWindow.postMessage({ type: 'book-reader-key', key: event.key, shiftKey: event.shiftKey }, contentOrigin);
  }
});
sections.addEventListener('change', () => showSection(Number(sections.value)));
$('toggle-settings').addEventListener('click', () => showDrawer('settings'));
$('toggle-notes').addEventListener('click', () => showDrawer('notes'));
$('settings-close').addEventListener('click', () => {
  showDrawer(null);
  $('toggle-settings').focus();
});
$('notes-close').addEventListener('click', () => {
  showDrawer(null);
  $('toggle-notes').focus();
});
for (const key of Object.keys(defaultSettings)) {
  $('setting-' + key).addEventListener('input', event => {
    const value = event.target.value;
    settings[key] = key === 'size' || key === 'spacing' ? Number(value) : value;
    applySettings();
  });
}
$('reset-settings').addEventListener('click', () => {
  settings = { ...defaultSettings };
  applySettings();
});
$('new-note').addEventListener('click', () => startNote());
$('cancel-note').addEventListener('click', cancelNote);
$('note-form').addEventListener('submit', event => {
  event.preventDefault();
  const text = $('note-text').value.trim();
  if (!text || !draftLocation) {
    $('reader-status').textContent = 'Enter a note before saving.';
    return;
  }
  const note = {
    id: editingNote || crypto.randomUUID(),
    location: draftLocation,
    quote: $('note-quote').textContent,
    text,
  };
  const updated = editingNote ? notes.map(item => item.id === editingNote ? note : item) : [...notes, note];
  if (writeNotes(updated)) {
    cancelNote();
    renderNotes();
  }
});
$('search').addEventListener('input', renderLibrary);
function canLeaveDraft() {
  return $('note-form').hidden || $('note-text').value === draftInitialText || confirm('Discard the note you are editing?');
}
window.addEventListener('popstate', () => {
  if (!canLeaveDraft()) {
    if (currentBook) history.pushState(null, '', '?book=' + encodeURIComponent(currentBook.id));
    return;
  }
  restoreLocation();
});
window.addEventListener('beforeunload', event => {
  if (!$('note-form').hidden && $('note-text').value !== draftInitialText) event.preventDefault();
});
window.addEventListener('pagehide', saveProgress);

loadSettings();
updateSettingsControls();
updateSortButton();
fetch('/api/books').then(response => {
  if (!response.ok) throw new Error('Server returned ' + response.status);
  return response.json();
}).then(data => {
  if (!Number.isInteger(data.contentPort) || data.contentPort < 1 || data.contentPort > 65535 ||
      String(data.contentPort) === location.port) throw new Error('Isolated EPUB content server is unavailable');
  contentOrigin = 'http://' + location.hostname + ':' + data.contentPort;
  books = data.books;
  libraryItems = buildLibraryItems();
  const issues = data.errors.map(error => error.file + ': ' + error.error);
  for (const book of books) {
    for (const warning of book.warnings) issues.push(book.title + ': ' + warning);
  }
  if (issues.length) {
    $('library-status').dataset.error = 'true';
    $('library-status').textContent = 'Book library warnings:\n' + issues.join('\n');
  }
  restoreLocation();
  loadDifficulty();
}).catch(error => {
  $('library-status').textContent = 'Could not load the book library: ' + error.message;
});
