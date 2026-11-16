'use strict';

// Jiten, Natively and jpdb information for library titles, shared by the manga and book libraries.
// The server looks titles up and caches them; a wrong or missing match can be fixed with a link.
window.Lookups = (() => {
  const SOURCES = [
    { id: 'jiten', name: 'Jiten', site: 'https://jiten.moe/', hint: 'https://jiten.moe/decks/media/12345/detail', kinds: ['manga', 'novel'] },
    { id: 'natively', name: 'Natively', site: 'https://learnnatively.com/', hint: 'https://learnnatively.com/series/…/', kinds: ['manga', 'novel'] },
    { id: 'jpdb', name: 'jpdb', site: 'https://jpdb.io/novel-difficulty-list', hint: 'https://jpdb.io/novel/1234/…', kinds: ['novel'] },
  ];
  let features = null;

  async function loadFeatures() {
    if (!features) {
      features = fetch('/api/features').then(response => response.ok ? response.json() : null).catch(() => null)
        .then(data => data || { lookups: { jiten: true, natively: true, jpdb: true }, canChange: false });
    }
    return features;
  }

  async function sourcesFor(kind) {
    const { lookups } = await loadFeatures();
    return SOURCES.filter(source => lookups[source.id] && source.kinds.includes(kind));
  }

  async function lookup(source, kind, title) {
    try {
      const response = await fetch(`/api/lookup?source=${source}&type=${kind}&title=${encodeURIComponent(title)}`);
      const data = await response.json().catch(() => null);
      if (!response.ok) return { source, result: null, error: data?.error || `HTTP ${response.status}` };
      return data;
    } catch (error) {
      return { source, result: null, error: error.message };
    }
  }

  // Looks every item up in every enabled source, a few requests at a time; onResult(item, source, info).
  async function lookupAll(items, kind, titleOf, onResult) {
    const sources = await sourcesFor(kind);
    const todo = items.flatMap(item => sources.map(source => [item, source.id]));
    let next = 0;
    async function worker() {
      while (next < todo.length) {
        const [item, source] = todo[next++];
        onResult(item, source, await lookup(source, kind, titleOf(item)));
      }
    }
    await Promise.all(Array.from({ length: 4 }, worker));
  }

  const number = value => typeof value === 'number' ? value.toLocaleString() : null;
  const fixed = (value, digits = 1) => typeof value === 'number' ? value.toFixed(digits) : null;

  // The value each source is sorted by, easiest first; null when unknown.
  function level(source, info) {
    const result = info?.result;
    if (!result) return null;
    if (source === 'natively') return typeof result.level === 'number' ? result.level : null;
    return typeof result.difficulty === 'number' ? result.difficulty : null;
  }

  function badge(source, info) {
    const value = level(source, info);
    if (value === null) return '';
    if (source === 'jiten') return `Jiten ${fixed(value)}`;
    if (source === 'natively') return `L${value}`;
    return `jpdb ${value}`;
  }

  function badges(infos) {
    return SOURCES.map(source => badge(source.id, infos?.[source.id])).filter(Boolean).join(' · ');
  }

  function details(source, result) {
    const parts = [];
    if (source === 'jiten') {
      parts.push(`Difficulty ${fixed(result.difficulty) ?? '?'}/5`);
      if (result.characters) parts.push(`${number(result.characters)} characters`);
      if (result.uniqueWords) parts.push(`${number(result.uniqueWords)} different words`);
      if (result.wordsFor?.['90%']) parts.push(`${number(result.wordsFor['90%'])} words for 90%, ${number(result.wordsFor['95%']) ?? '?'} for 95%`);
      if (typeof result.coverage === 'number') parts.push(`you know ${fixed(result.coverage)}% (Jiten)`);
    } else if (source === 'natively') {
      parts.push(result.levelText ? `Level ${result.levelText}` : `Level L${result.level ?? '?'}`);
      if (result.provisional) parts.push('provisional');
      if (result.series && result.volumes) parts.push(`${result.volumes} volumes`);
      if (typeof result.rating === 'number' && result.rating > 0) parts.push(`rated ${result.rating.toFixed(1)}/5`);
    } else {
      parts.push(`Difficulty ${result.difficulty ?? '?'}/100`);
      if (result.peak != null) parts.push(`peak ${result.peak}`);
      if (result.characters) parts.push(`${number(result.characters)} characters`);
      if (result.uniqueWords) parts.push(`${number(result.uniqueWords)} different words`);
    }
    return parts.join(' · ');
  }

  async function saveLink(source, kind, title, link) {
    const response = await fetch('/api/lookup-link', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ source, type: kind, title, link }),
    });
    const data = await response.json().catch(() => null);
    if (!response.ok) throw new Error(data?.error || (response.status === 403 ? 'Matches can only be changed on the computer running the reader.' : `HTTP ${response.status}`));
    return data;
  }

  // One row per source: what was found, a link to it, and a way to fix the match.
  // infos: {source: answer | undefined while loading}; onChange(source, info) after a fix.
  async function renderSection(container, kind, title, infos, onChange) {
    const sources = await sourcesFor(kind);
    const { canChange } = await loadFeatures();
    container.replaceChildren();
    if (!sources.length) {
      container.hidden = true;
      return;
    }
    container.hidden = false;
    const heading = document.createElement('h3');
    heading.textContent = 'Difficulty';
    container.append(heading);
    for (const source of sources) {
      const info = infos?.[source.id];
      const row = document.createElement('div');
      row.className = 'lookup-row';
      const name = document.createElement('span');
      name.className = 'lookup-source';
      name.textContent = source.name;
      const body = document.createElement('div');
      body.className = 'lookup-body';
      const line = document.createElement('div');
      if (info === undefined) {
        line.textContent = 'Looking up…';
      } else if (info.result) {
        const link = document.createElement('a');
        link.href = info.result.url;
        link.target = '_blank';
        link.rel = 'noopener noreferrer';
        link.textContent = (info.result.title || 'Open') + ' ↗';
        const text = document.createElement('div');
        text.className = 'lookup-details';
        text.textContent = details(source.id, info.result);
        line.append(link, text);
      } else if (info.error) {
        line.textContent = 'Could not look this up: ' + info.error;
      } else {
        line.textContent = info.manual ? 'Marked as not on ' + source.name : 'Not found';
      }
      body.append(line);
      if (info !== undefined && canChange) body.append(fixForm(source, kind, title, info, onChange));
      row.append(name, body);
      container.append(row);
    }
  }

  function fixForm(source, kind, title, info, onChange) {
    const details = document.createElement('details');
    details.className = 'lookup-fix';
    const summary = document.createElement('summary');
    summary.textContent = info.result ? (info.manual ? 'Linked by hand · change' : 'Wrong title?') : 'Add the link';
    const form = document.createElement('form');
    const input = document.createElement('input');
    input.type = 'url';
    input.placeholder = source.hint;
    input.setAttribute('aria-label', `${source.name} link for ${title}`);
    const save = document.createElement('button');
    save.type = 'submit';
    save.className = 'secondary';
    save.textContent = 'Use link';
    const none = document.createElement('button');
    none.type = 'button';
    none.className = 'ghost';
    none.textContent = 'Not on ' + source.name;
    const again = document.createElement('button');
    again.type = 'button';
    again.className = 'ghost';
    again.textContent = 'Search again';
    again.hidden = !info.manual;
    const find = document.createElement('a');
    find.href = source.id === 'jpdb' ? `https://jpdb.io/novel-difficulty-list?q=${encodeURIComponent(title)}` :
      source.id === 'natively' ? `https://learnnatively.com/search/jpn/books/?q=${encodeURIComponent(title)}` :
        `https://jiten.moe/decks/media?titleFilter=${encodeURIComponent(title)}`;
    find.target = '_blank';
    find.rel = 'noopener noreferrer';
    find.textContent = `Search ${source.name} ↗`;
    const status = document.createElement('p');
    status.className = 'lookup-status';
    status.setAttribute('role', 'status');
    async function send(link) {
      status.textContent = 'Saving…';
      try {
        onChange(source.id, await saveLink(source.id, kind, title, link));
      } catch (error) {
        status.textContent = error.message;
      }
    }
    form.addEventListener('submit', event => {
      event.preventDefault();
      if (input.value.trim()) send(input.value.trim());
    });
    none.addEventListener('click', () => send('none'));
    again.addEventListener('click', () => send(''));
    const buttons = document.createElement('div');
    buttons.className = 'lookup-buttons';
    buttons.append(save, none, again, find);
    form.append(input, buttons, status);
    details.append(summary, form);
    return details;
  }

  return { SOURCES, loadFeatures, sourcesFor, lookup, lookupAll, level, badge, badges, details, renderSection };
})();
