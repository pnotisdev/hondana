// ==UserScript==
// @name         Anime-Planet Japanese Difficulty Sorter
// @namespace    https://github.com/pnotis
// @version      0.5.0
// @description  Sort Anime-Planet anime/manga lists by JPDB/Jiten Japanese difficulty, and show difficulty on individual anime/manga pages.
// @author       pnotis
// @match        https://www.anime-planet.com/users/*/anime/wanttowatch*
// @match        https://www.anime-planet.com/users/*/anime/watched*
// @match        https://www.anime-planet.com/users/*/anime/watching*
// @match        https://www.anime-planet.com/users/*/anime/stalled*
// @match        https://www.anime-planet.com/users/*/anime/dropped*
// @match        https://www.anime-planet.com/users/*/anime/wontwatch*
// @match        https://www.anime-planet.com/anime/all*
// @match        https://www.anime-planet.com/anime/*
// @match        https://www.anime-planet.com/users/*/manga/read*
// @match        https://www.anime-planet.com/users/*/manga/reading*
// @match        https://www.anime-planet.com/users/*/manga/wanttoread*
// @match        https://www.anime-planet.com/users/*/manga/stalled*
// @match        https://www.anime-planet.com/users/*/manga/dropped*
// @match        https://www.anime-planet.com/users/*/manga/wontread*
// @match        https://www.anime-planet.com/manga/all*
// @match        https://www.anime-planet.com/manga/*
// @grant        GM_xmlhttpRequest
// @grant        GM_getValue
// @grant        GM_setValue
// @connect      jpdb.io
// @connect      api.jiten.moe
// @connect      jiten.moe
// ==/UserScript==

(() => {
    'use strict';

    /********************************************************************
     * CONFIG
     ********************************************************************/

    const CONFIG = {
        VERSION: '0.5.0',
        CACHE_KEY: 'ap-japanese-difficulty-cache-v034',
        JPDB_URL: 'https://jpdb.io/anime-difficulty-list',
        JITEN_API: 'https://api.jiten.moe',
        JPDB_CONCURRENCY: 5,
        JITEN_CONCURRENCY: 2,
        MATCH_THRESHOLD: 0.72,
        DETECTION_TIMEOUT: 20000,
        // How many ancestor levels above each anime/manga anchor to search
        // when trying to find the repeating "row"/"card" element for the
        // list. Anime-Planet's table view nests the anchor two levels deep
        // (a -> td -> tr), so this needs to comfortably exceed that.
        MAX_STRUCTURE_DEPTH: 10,

        /*
         * Jiten's media-deck objects carry a numeric `mediaType` field.
         * Confirmed empirically against known titles (Bocchi the Rock,
         * Attack on Titan, Berserk, Yotsuba&!): 1 = anime, 9 = manga.
         * JPDB has no manga difficulty data at all (jpdb.io/manga-difficulty-list
         * 404s, and there's no "Manga" entry in its "other difficulty
         * lists" — the difficulty lists are anime/live-action/novels/VN/
         * web-novels only), so JPDB is anime-only in this script.
         */
        JITEN_MEDIA_TYPE: { anime: 1, manga: 9 },

        // First path segment after /anime/ or /manga/ that is a real
        // Anime-Planet route, not a title slug — used to avoid treating
        // e.g. /manga/all or /anime/recommendations as a detail page.
        RESERVED_SLUGS: {
            anime: new Set([
                'all', 'recommendations', 'top-anime', 'random', 'search',
                'seasonal', 'studios', 'years', 'tags', 'characters', 'people'
            ]),
            manga: new Set([
                'all', 'recommendations', 'top-manga', 'random', 'search',
                'read-online', 'webtoons', 'light-novels', 'tags',
                'magazines', 'characters', 'people'
            ])
        }
    };

    /********************************************************************
     * PAGE CONTEXT
     *
     * Figures out (a) whether we're looking at anime or manga, and
     * (b) whether this is a "list" page (a user's list, or the browse-all
     * page — many items to sort) or a "detail" page (one single
     * anime/manga — just show its difficulty inline). Returns null when
     * neither applies, so the script can no-op cleanly on pages it was
     * only injected into because of a broad @match (e.g. /manga/tags/x).
     ********************************************************************/

    function detectContext() {
        const path = location.pathname;

        let m = path.match(/^\/users\/[^/]+\/(anime|manga)\//);
        if (m) {
            return { type: m[1], kind: 'list' };
        }

        m = path.match(/^\/(anime|manga)\/all\b/);
        if (m) {
            return { type: m[1], kind: 'list' };
        }

        m = path.match(/^\/(anime|manga)\/([^/]+)\/?$/);
        if (m) {
            const [, type, slug] = m;
            if (!CONFIG.RESERVED_SLUGS[type].has(slug)) {
                return { type, kind: 'detail', slug };
            }
        }

        return null;
    }

    const CONTEXT = detectContext();

    /********************************************************************
     * STATE
     ********************************************************************/

    let animeItems = [];
    let listParent = null;
    let observer = null;
    let detectionFinished = false;
    let jpdbLoaded = false;
    let jitenLoaded = false;

    /********************************************************************
     * STORAGE
     ********************************************************************/

    function readCache() {
        return GM_getValue(CONFIG.CACHE_KEY, {});
    }

    function writeCache(cache) {
        GM_setValue(CONFIG.CACHE_KEY, cache);
    }

    /*
     * One-time cleanups. Earlier versions cached bad/empty Jiten data
     * under various bugs; those migrations strip stale `jiten` fields so
     * they get re-fetched, without throwing away already-working JPDB
     * matches by bumping CACHE_KEY.
     *
     * `mediaPrefixKeys` handles the anime->anime+manga upgrade: every
     * cache entry before this version was necessarily an anime title
     * (the script only ever ran on anime pages), but now that titles can
     * be looked up as either anime or manga, keys need a media prefix so
     * e.g. a manga and an anime that happen to share a normalized title
     * don't collide. Rewriting old keys as `anime:<key>` preserves them
     * exactly instead of discarding everything.
     */
    function migrateCache() {
        let cache = readCache();
        let changed = false;

        if (!cache.__migrations) {
            cache.__migrations = {};
            changed = true;
        }

        if (!cache.__migrations.jitenAltTitles) {
            for (const key of Object.keys(cache)) {
                if (key === '__migrations') {
                    continue;
                }
                if (cache[key] && 'jiten' in cache[key]) {
                    delete cache[key].jiten;
                }
            }
            cache.__migrations.jitenAltTitles = true;
            changed = true;
        }

        if (!cache.__migrations.jitenDetailFetch) {
            for (const key of Object.keys(cache)) {
                if (key === '__migrations') {
                    continue;
                }
                if (cache[key] && 'jiten' in cache[key]) {
                    delete cache[key].jiten;
                }
            }
            cache.__migrations.jitenDetailFetch = true;
            changed = true;
        }

        if (!cache.__migrations.mediaPrefixKeys) {
            const rebuilt = { __migrations: cache.__migrations };
            for (const [key, value] of Object.entries(cache)) {
                if (key === '__migrations') {
                    continue;
                }
                rebuilt[`anime:${key}`] = value;
            }
            cache = rebuilt;
            cache.__migrations.mediaPrefixKeys = true;
            changed = true;
        }

        if (changed) {
            writeCache(cache);
        }
    }

    function normalize(value) {
        return String(value || '')
            .normalize('NFKC')
            .toLowerCase()
            .replace(/[’‘`´]/g, "'")
            .replace(/[“”]/g, '"')
            .replace(/[「」『』]/g, ' ')
            .replace(/[()[\]{}:;,.!?/\\|]/g, ' ')
            .replace(/[-‐‑‒–—―]/g, ' ')
            .replace(/\s+/g, ' ')
            .trim();
    }

    function cacheKey(mediaType, title) {
        return `${mediaType}:${normalize(title)}`;
    }

    function getCached(mediaType, title) {
        const cache = readCache();
        return cache[cacheKey(mediaType, title)] || null;
    }

    function setCached(mediaType, title, value) {
        const cache = readCache();
        const key = cacheKey(mediaType, title);
        cache[key] = {
            ...(cache[key] || {}),
            ...value,
            cachedAt: Date.now()
        };
        writeCache(cache);
    }

    /********************************************************************
     * HTTP
     ********************************************************************/

    function request(url) {
        return new Promise((resolve, reject) => {
            GM_xmlhttpRequest({
                method: 'GET',
                url,
                timeout: 15000,
                headers: {
                    Accept: 'text/html,application/json'
                },
                onload(response) {
                    if (response.status >= 200 && response.status < 400) {
                        resolve(response.responseText);
                    } else {
                        reject(new Error(`HTTP ${response.status}`));
                    }
                },
                onerror() {
                    reject(new Error('Network error'));
                },
                ontimeout() {
                    reject(new Error('Request timeout'));
                }
            });
        });
    }

    /*
     * Like request(), but supports method + a JSON body — used for
     * probing candidate API shapes where the real one might be a POST
     * with a JSON payload rather than GET query params.
     */
    function requestJSON({ method = 'GET', url, body, accept = 'application/json' }) {
        return new Promise((resolve, reject) => {
            GM_xmlhttpRequest({
                method,
                url,
                timeout: 15000,
                headers: {
                    Accept: accept,
                    ...(body ? { 'Content-Type': 'application/json' } : {})
                },
                data: body ? JSON.stringify(body) : undefined,
                onload(response) {
                    if (response.status >= 200 && response.status < 400) {
                        resolve(response.responseText);
                    } else {
                        reject(
                            new Error(
                                `HTTP ${response.status}: ${response.responseText?.slice(0, 200) || ''}`
                            )
                        );
                    }
                },
                onerror() {
                    reject(new Error('Network error'));
                },
                ontimeout() {
                    reject(new Error('Request timeout'));
                }
            });
        });
    }

    /********************************************************************
     * UI (list pages)
     ********************************************************************/

    const CSS = `
        #ap-jp-difficulty {
            box-sizing: border-box;
            margin: 14px 0;
            padding: 12px 14px;
            border-radius: 6px;
            background: #202020;
            color: #eee;
            font-family: inherit;
            position: relative;
            z-index: 9999;
        }
        #ap-jp-difficulty * {
            box-sizing: border-box;
        }
        #ap-jp-difficulty-title {
            font-weight: 700;
            margin-bottom: 8px;
        }
        #ap-jp-difficulty-buttons {
            display: flex;
            flex-wrap: wrap;
            gap: 5px;
        }
        #ap-jp-difficulty button {
            border: 1px solid #555;
            border-radius: 4px;
            background: #2c2c2c;
            color: #eee;
            padding: 6px 9px;
            cursor: pointer;
            font: inherit;
            font-size: 12px;
        }
        #ap-jp-difficulty button:hover {
            background: #3a3a3a;
        }
        #ap-jp-difficulty button:disabled {
            opacity: .45;
            cursor: default;
        }
        #ap-jp-difficulty button.active {
            border-color: #aaa;
            background: #4a4a4a;
        }
        #ap-jp-difficulty-status {
            margin-top: 8px;
            font-size: 12px;
            opacity: .8;
        }
        #ap-jp-debug {
            display: none;
            margin-top: 10px;
            padding: 8px;
            background: #111;
            color: #ddd;
            border-radius: 4px;
            font: 10px/1.4 monospace;
            white-space: pre-wrap;
            max-height: 400px;
            overflow: auto;
        }
        #ap-jp-detail-badge {
            margin: 8px 0 14px;
            font-size: 13px;
        }
        #ap-jp-detail-badge.ap-jp-badge-empty {
            opacity: .6;
        }
        .ap-jp-badges {
            display: inline-flex;
            flex-wrap: wrap;
            gap: 3px;
            margin-left: 6px;
            vertical-align: middle;
        }
        .ap-jp-badge {
            display: inline-block;
            padding: 2px 5px;
            border-radius: 3px;
            background: rgba(0,0,0,.15);
            border: 1px solid rgba(150,150,150,.4);
            font-size: 10px;
            line-height: 1.2;
            white-space: nowrap;
            text-decoration: none;
            color: inherit;
        }
        .ap-jp-badge.unknown {
            opacity: .4;
        }
        #ap-jp-detail-badge .ap-jp-badge {
            font-size: 12px;
            padding: 3px 7px;
            margin-right: 4px;
        }
    `;

    function installCSS() {
        if (document.getElementById('ap-jp-difficulty-css')) {
            return;
        }
        const style = document.createElement('style');
        style.id = 'ap-jp-difficulty-css';
        style.textContent = CSS;
        document.head.appendChild(style);
    }

    function createUI() {
        if (document.getElementById('ap-jp-difficulty')) {
            return;
        }

        const isAnime = CONTEXT.type === 'anime';
        const label = isAnime ? 'anime' : 'manga';

        const ui = document.createElement('section');
        ui.id = 'ap-jp-difficulty';
        ui.innerHTML = `
            <div id="ap-jp-difficulty-title">
                Japanese difficulty (${label})
            </div>
            <div id="ap-jp-difficulty-buttons">
                <button type="button" data-action="load">
                    Load difficulty
                </button>
                ${isAnime ? `
                <button type="button" data-sort="jpdb-asc" disabled>
                    JPDB ↑ easiest
                </button>
                <button type="button" data-sort="jpdb-desc" disabled>
                    JPDB ↓ hardest
                </button>
                ` : ''}
                <button type="button" data-sort="jiten-asc" disabled>
                    Jiten ↑ easiest
                </button>
                <button type="button" data-sort="jiten-desc" disabled>
                    Jiten ↓ hardest
                </button>
                ${isAnime ? `
                <button type="button" data-sort="combined-asc" disabled>
                    Combined ↑
                </button>
                ` : ''}
                <button type="button" data-sort="original">
                    Original order
                </button>
                <button type="button" data-action="debug">
                    Debug
                </button>
                ${isAnime ? `
                <button type="button" data-action="test-jpdb">
                    Test JPDB
                </button>
                ` : ''}
                <button type="button" data-action="test-jiten">
                    Probe Jiten API
                </button>
            </div>
            <div id="ap-jp-difficulty-status">
                Waiting for Anime-Planet…
            </div>
            <pre id="ap-jp-debug"></pre>
        `;

        /*
         * Put it before the first major content area.
         */
        const target =
            document.querySelector('main') ||
            document.querySelector('#site-content') ||
            document.body;
        target.prepend(ui);

        ui.addEventListener('click', event => {
            const button = event.target.closest('button');
            if (!button) {
                return;
            }
            if (button.dataset.action === 'load') {
                loadDifficulty();
                return;
            }
            if (button.dataset.action === 'debug') {
                showDebug();
                return;
            }
            if (button.dataset.action === 'test-jpdb') {
                testJPDB();
                return;
            }
            if (button.dataset.action === 'test-jiten') {
                probeJitenAPI();
                return;
            }
            if (button.dataset.sort) {
                sortItems(button.dataset.sort);
            }
        });
    }

    function setStatus(text) {
        const el = document.getElementById('ap-jp-difficulty-status');
        if (el) {
            el.textContent = text;
        }
    }

    /********************************************************************
     * ANIME-PLANET LIST DETECTION
     ********************************************************************/

    /*
     * This is the important selector based on the HTML for anime rows.
     * Manga cards don't carry `data-class="tooltip-full"` (they use a
     * plain `class="tooltip manga12345"` instead), so this exact
     * selector only ever matches on anime lists — manga lists fall
     * through to the broad fallback below, which works for both.
     */
    function getMediaAnchors(seg) {
        const exact = [
            ...document.querySelectorAll(
                `a.tooltip[data-class="tooltip-full"][href*="/${seg}/"]`
            )
        ];
        if (exact.length >= 2) {
            return exact;
        }

        /*
         * Fallback in case Anime-Planet changes the tooltip class, or
         * (as with manga card view) never used it for this media type.
         *
         * Requiring a non-empty `title` attribute is what actually makes
         * this safe to use broadly: every real anime/manga card carries
         * the rich tooltip preview in `title` (500+ chars, confirmed
         * live), while every site-nav link matching the same href shape
         * (e.g. `/manga/all`, `/manga/read-online/`, `/manga/recommendations/`)
         * has no `title` attribute at all. Without this check those nav
         * links get misidentified as list entries on pages where the
         * exact selector doesn't match (e.g. manga card-view lists).
         */
        const broad = [...document.querySelectorAll(`a[href*="/${seg}/"]`)];
        return broad.filter(link => {
            const href = link.getAttribute('href') || '';
            const text = link.textContent.replace(/\s+/g, ' ').trim();
            return (
                new RegExp(`^/${seg}/[^/?#]+/?$`).test(href) &&
                text.length >= 2 &&
                text.length <= 300 &&
                link.hasAttribute('title')
            );
        });
    }

    function getSlug(link, seg) {
        const href = link.getAttribute('href');
        if (!href) {
            return null;
        }
        const match = href.match(new RegExp(`^/${seg}/([^/?#]+)/?$`));
        return match ? match[1] : null;
    }

    /*
     * Anime-Planet's tooltip markup embeds both the canonical title and
     * the alt/Japanese title, e.g. `<h5 class='theme-font'>Yotsuba&!</h5>
     * <h6 class='theme-font tooltip-alt'>Alt title: Yotsuba to!,
     * よつばと!</h6>`. Confirmed identical markup on manga cards.
     *
     * The canonical title MUST come from here rather than the anchor's
     * own `.textContent`: on card-view lists (used by manga, and
     * possibly by anime lists too depending on the user's view
     * preference) the whole card — including a "Read" status label and
     * a numeric rating — is wrapped in one `<a>`, so
     * `anchor.textContent` comes out as something like
     * "Read3.5 Belladonna no Koibito" instead of just the title. The
     * tooltip's `<h5>` doesn't have that problem since it's built
     * server-side purely from the title.
     */
    function decodeHTMLEntities(str) {
        const el = document.createElement('textarea');
        el.innerHTML = str;
        return el.value;
    }

    function getTooltipInfo(anchor) {
        const raw = anchor.getAttribute('title') || '';

        const titleMatch = raw.match(/<h5[^>]*>([\s\S]*?)<\/h5>/i);
        const title = titleMatch
            ? decodeHTMLEntities(titleMatch[1]).replace(/\s+/g, ' ').trim()
            : null;

        const altMatch = raw.match(/<h6[^>]*tooltip-alt[^>]*>([\s\S]*?)<\/h6>/i);
        const altTitles = altMatch
            ? decodeHTMLEntities(altMatch[1])
                .replace(/^Alt titles?:\s*/i, '')
                .trim()
                .split(',')
                .map(part => part.trim())
                .filter(Boolean)
            : [];

        return { title, altTitles };
    }

    /*
     * Step 1: collect every valid anime/manga anchor on the page,
     * deduplicated by slug, in document order. This does NOT yet know
     * anything about rows, cards, or tables — it's just "here are the
     * media links."
     */
    function buildAnchorCandidates(seg) {
        const anchors = getMediaAnchors(seg);
        const list = [];
        const seen = new Set();
        for (const anchor of anchors) {
            const slug = getSlug(anchor, seg);
            if (!slug || seen.has(slug)) {
                continue;
            }
            const tooltip = getTooltipInfo(anchor);
            const title =
                tooltip.title ||
                anchor.textContent.replace(/\s+/g, ' ').trim();
            if (!title || title.length > 200) {
                continue;
            }
            seen.add(slug);
            list.push({
                link: anchor,
                slug,
                title,
                altTitles: tooltip.altTitles,
                originalIndex: list.length
            });
        }
        return list;
    }

    /*
     * Step 2: figure out which ancestor level is the "row"/"card" that
     * repeats for each item, and what its shared parent (the list
     * container) is.
     *
     * IMPORTANT: an anchor is not necessarily a direct child of the
     * repeating row. On Anime-Planet's table list view the structure is
     * `<tr><td class="tableTitle"><a>Title</a></td>...</tr>` — the
     * anchor sits two levels *inside* the row, and it's the <tr>
     * elements that repeat as siblings under <tbody>, not the anchors
     * themselves.
     *
     * So instead of checking "is this anchor a direct child of X", we
     * walk up 0..MAX_STRUCTURE_DEPTH levels from each anchor, and at
     * each depth group anchors by their ancestor's parent. Whichever
     * (depth, parent) pair captures the most distinct ancestor nodes
     * under one shared parent wins — that parent is the list container,
     * and the ancestor at that depth is the row/card to move when
     * sorting.
     */
    function resolveListStructure(anchorCandidates) {
        let best = null; // { depth, parent, count }

        for (let depth = 0; depth <= CONFIG.MAX_STRUCTURE_DEPTH; depth++) {
            const groups = new Map(); // parent -> Set of ancestor nodes at this depth

            for (const item of anchorCandidates) {
                let node = item.link;
                let reachable = true;
                for (let i = 0; i < depth; i++) {
                    if (!node.parentElement) {
                        reachable = false;
                        break;
                    }
                    node = node.parentElement;
                }
                if (!reachable) {
                    continue;
                }

                const parent = node.parentElement;
                if (!parent || parent === document.documentElement) {
                    continue;
                }

                if (!groups.has(parent)) {
                    groups.set(parent, new Set());
                }
                groups.get(parent).add(node);
            }

            for (const [parent, nodes] of groups) {
                const count = nodes.size;
                if (count < 2) {
                    continue;
                }
                if (
                    !best ||
                    count > best.count ||
                    (count === best.count && depth < best.depth)
                ) {
                    best = { depth, parent, count };
                }
            }
        }

        return best;
    }

    function detectAnimeList() {
        const anchorCandidates = buildAnchorCandidates(CONTEXT.type);
        if (anchorCandidates.length < 2) {
            return false;
        }

        const structure = resolveListStructure(anchorCandidates);
        if (!structure) {
            return false;
        }

        const items = [];
        for (const item of anchorCandidates) {
            let node = item.link;
            let reachable = true;
            for (let i = 0; i < structure.depth; i++) {
                if (!node.parentElement) {
                    reachable = false;
                    break;
                }
                node = node.parentElement;
            }
            if (!reachable || node.parentElement !== structure.parent) {
                continue;
            }
            items.push({
                ...item,
                card: node,
                result: null
            });
        }

        if (items.length < 2) {
            return false;
        }

        /*
         * At least half of the discovered anchors should belong to this
         * list.
         */
        if (items.length / anchorCandidates.length < 0.5) {
            console.warn('[AP-JP] Ambiguous list:', {
                candidates: anchorCandidates.length,
                direct: items.length
            });
            return false;
        }

        listParent = structure.parent;
        animeItems = items;

        console.log('[AP-JP] Successfully detected list:', {
            type: CONTEXT.type,
            items: items.length,
            parent: listParent.tagName,
            class: listParent.className,
            cardTag: items[0]?.card.tagName,
            depth: structure.depth
        });

        return true;
    }

    function startDetection() {
        const started = Date.now();

        function attempt() {
            if (detectionFinished) {
                return;
            }
            if (detectAnimeList()) {
                detectionFinished = true;
                if (observer) {
                    observer.disconnect();
                    observer = null;
                }
                setStatus(
                    `Found ${animeItems.length} ${CONTEXT.type}. Click "Load difficulty".`
                );
                return;
            }
            if (Date.now() - started > CONFIG.DETECTION_TIMEOUT) {
                detectionFinished = true;
                if (observer) {
                    observer.disconnect();
                    observer = null;
                }
                setStatus('Could not safely identify the list. Click Debug.');
                return;
            }
            setTimeout(attempt, 400);
        }

        /*
         * Anime-Planet can insert the list after document-idle.
         * Watch only until detection succeeds.
         */
        observer = new MutationObserver(() => {
            attempt();
        });
        observer.observe(document.body, {
            childList: true,
            subtree: true
        });

        attempt();
    }

    /********************************************************************
     * DEBUG
     ********************************************************************/

    function showDebug() {
        const output = document.getElementById('ap-jp-debug');
        if (!output) {
            return;
        }
        output.style.display = 'block';

        const seg = CONTEXT.type;
        const anchors = getMediaAnchors(seg);

        output.textContent = JSON.stringify(
            {
                url: location.href,
                mediaType: CONTEXT.type,
                exactSelectorCount: document.querySelectorAll(
                    `a.tooltip[data-class="tooltip-full"][href*="/${seg}/"]`
                ).length,
                broadSelectorCount:
                    document.querySelectorAll(`a[href*="/${seg}/"]`).length,
                detectedItems: animeItems.map(item => ({
                    title: item.title,
                    href: item.link.getAttribute('href'),
                    cardTag: item.card.tagName,
                    cardClass: item.card.className
                })),
                rawAnchors: anchors.slice(0, 10).map(anchor => ({
                    text: anchor.textContent.trim(),
                    href: anchor.getAttribute('href'),
                    class: anchor.className,
                    parent: anchor.parentElement?.tagName,
                    parentClass: anchor.parentElement?.className,
                    grandparent: anchor.parentElement?.parentElement?.tagName,
                    grandparentClass:
                        anchor.parentElement?.parentElement?.className
                })),
                listParent: listParent
                    ? {
                        tag: listParent.tagName,
                        class: listParent.className,
                        childCount: listParent.children.length
                    }
                    : null
            },
            null,
            2
        );
    }

    /********************************************************************
     * TITLE MATCHING
     ********************************************************************/

    function tokens(value) {
        return normalize(value)
            .split(' ')
            .filter(x => x.length > 1);
    }

    /*
     * "ou"->"o", "uu"->"u" — the two unambiguous long-vowel romanizations
     * (お段+う, う段+う). Deliberately not touching "ei"->"e": that pattern
     * is far more often a real distinct sequence (e.g. "keitai") than a
     * long-vowel marker, so collapsing it risks false positive matches.
     */
    function collapseLongVowels(value) {
        return value.replace(/ou/g, 'o').replace(/uu/g, 'u');
    }

    function similarity(a, b) {
        const aa = normalize(a);
        const bb = normalize(b);
        if (!aa || !bb) {
            return 0;
        }
        if (aa === bb) {
            return 1;
        }
        if (aa.length >= 5 && (aa.includes(bb) || bb.includes(aa))) {
            return 0.92;
        }
        /*
         * Catches cases like "Yotsubato" vs JPDB/Jiten's "Yotsuba to!" —
         * same title, just missing/extra whitespace between words.
         */
        const squeezedA = aa.replace(/ /g, '');
        const squeezedB = bb.replace(/ /g, '');
        if (squeezedA.length >= 5 && squeezedA === squeezedB) {
            return 0.9;
        }
        /*
         * Catches romanization variance like "Kyoko" vs official "Kyouko".
         */
        const collapsedA = collapseLongVowels(squeezedA);
        const collapsedB = collapseLongVowels(squeezedB);
        if (collapsedA.length >= 5 && collapsedA === collapsedB) {
            return 0.85;
        }
        const aTokens = new Set(tokens(aa));
        const bTokens = new Set(tokens(bb));
        let common = 0;
        for (const token of aTokens) {
            if (bTokens.has(token)) {
                common++;
            }
        }
        const union = new Set([...aTokens, ...bTokens]).size;
        return union ? common / union : 0;
    }

    /*
     * Jiten's actual deck schema (confirmed from a real API response)
     * uses `romajiTitle` / `englishTitle` / `originalTitle` — there is no
     * generic `title` or `name` field. Check all of them and keep
     * whichever gives the best match, since a query might hit the
     * romaji form, the English form, or (if we ever pass native script)
     * the original Japanese title.
     */
    function getJitenTitles(result) {
        return [
            result?.romajiTitle,
            result?.englishTitle,
            result?.originalTitle,
            result?.title,
            result?.name
        ].filter(Boolean);
    }

    function bestJitenSimilarity(query, result) {
        let best = 0;
        for (const candidateTitle of getJitenTitles(result)) {
            const score = similarity(query, candidateTitle);
            if (score > best) {
                best = score;
            }
        }
        return best;
    }

    /********************************************************************
     * JPDB (anime only — confirmed no manga difficulty data exists)
     ********************************************************************/

    /*
     * `?search=` is silently ignored by jpdb.io — it just returns the
     * site's default unfiltered difficulty list regardless of the query,
     * so it's not useful here. `?q=` is the parameter the site's own
     * search form actually reads.
     */
    async function searchJPDB(query) {
        const url = `${CONFIG.JPDB_URL}?q=${encodeURIComponent(query)}`;
        try {
            const html = await request(url);
            return parseJPDB(html);
        } catch (error) {
            console.debug('[AP-JP] JPDB request failed:', error);
            return [];
        }
    }

    function parseJPDB(html) {
        const doc = new DOMParser().parseFromString(html, 'text/html');
        const results = [];
        for (const heading of doc.querySelectorAll('h1,h2,h3,h4,h5,h6')) {
            const title = heading.textContent.replace(/\s+/g, ' ').trim();
            if (title.length < 2) {
                continue;
            }
            let node = heading;
            for (let i = 0; i < 4; i++) {
                node = node.parentElement;
                if (!node) {
                    break;
                }
                const text = node.textContent.replace(/\s+/g, ' ');
                const average = text.match(
                    /Average difficulty\s*:?\s*(\d+)\s*\/\s*100/i
                );
                if (!average) {
                    continue;
                }
                const peak = text.match(
                    /Peak difficulty[^0-9]*(\d+)\s*\/\s*100/i
                );
                results.push({
                    title,
                    average: Number(average[1]),
                    peak: peak ? Number(peak[1]) : null
                });
                break;
            }
        }
        return results;
    }

    /*
     * DIAGNOSTIC: fetches one real JPDB response and reports exactly what
     * came back, so we can see whether the request failed, the page
     * returned an empty JS-app shell (no server-rendered data), or the
     * markup just doesn't match what parseJPDB expects anymore.
     */
    async function testJPDB() {
        const output = document.getElementById('ap-jp-debug');
        if (!output) {
            return;
        }
        output.style.display = 'block';
        output.textContent = 'Testing JPDB…';

        const sampleItem = animeItems[0] || {
            title: 'A Bridge to the Starry Skies',
            altTitles: ['Hoshizora e Kakaru Hashi']
        };
        const queries = [...(sampleItem.altTitles || []), sampleItem.title];
        const report = {
            sampleTitle: sampleItem.title,
            altTitles: sampleItem.altTitles || [],
            attempts: []
        };

        for (const query of queries) {
            const url = `${CONFIG.JPDB_URL}?q=${encodeURIComponent(query)}`;
            try {
                const html = await request(url);
                const parsed = parseJPDB(html);
                const scored = parsed
                    .map(result => ({
                        title: result.title,
                        average: result.average,
                        score: similarity(query, result.title)
                    }))
                    .sort((a, b) => b.score - a.score)
                    .slice(0, 5);

                report.attempts.push({
                    query,
                    url,
                    status: 'ok',
                    responseLength: html.length,
                    parsedResultCount: parsed.length,
                    topMatches: scored,
                    responseSnippetStart: html.slice(0, 600)
                });
            } catch (error) {
                report.attempts.push({
                    query,
                    url,
                    status: 'error',
                    error: error.message
                });
            }
        }

        output.textContent = JSON.stringify(report, null, 2);
        console.log('[AP-JP] JPDB test report:', report);
    }

    /*
     * Tries each candidate query (alt/Japanese titles first, since JPDB
     * indexes anime that way; the AP English title as a fallback for
     * shows that are already Japanese-named, like "Air") and keeps
     * whichever query's best result scores highest.
     */
    async function getJPDB(item) {
        const cacheTitle = item.title;
        const cached = getCached('anime', cacheTitle);
        if (cached && 'jpdb' in cached) {
            return cached.jpdb;
        }

        const queries = [...(item.altTitles || []), item.title];
        let best = null;
        let bestScore = 0;

        for (const query of queries) {
            const results = await searchJPDB(query);
            for (const result of results) {
                const score = similarity(query, result.title);
                if (score > bestScore) {
                    bestScore = score;
                    best = result;
                }
            }
            if (bestScore >= CONFIG.MATCH_THRESHOLD) {
                break;
            }
        }

        let final = null;
        if (best && bestScore >= CONFIG.MATCH_THRESHOLD) {
            final = {
                ...best,
                matchScore: bestScore
            };
        }

        setCached('anime', cacheTitle, { jpdb: final });
        return final;
    }

    /********************************************************************
     * JITEN (anime + manga)
     ********************************************************************/

    /*
     * Jiten's API structure may change. Keep this isolated so changing
     * the endpoint/parser doesn't affect Anime-Planet.
     *
     * `expectedMediaType` filters out cross-media collisions — e.g.
     * searching "Attack on Titan" returns both the anime (mediaType 1)
     * and manga (mediaType 9) decks under near-identical titles, and
     * without this filter the wrong one could win on title similarity
     * alone.
     */
    async function searchJiten(query, expectedMediaType) {
        const url =
            `${CONFIG.JITEN_API}` +
            `/api/media-deck/search-suggestions` +
            `?query=${encodeURIComponent(query)}`;
        try {
            const text = await request(url);
            const data = JSON.parse(text);
            const list = Array.isArray(data)
                ? data
                : data.suggestions || data.results || data.items || [];
            return list.filter(result => result?.mediaType === expectedMediaType);
        } catch (error) {
            console.debug('[AP-JP] Jiten request failed:', error);
            return [];
        }
    }

    /*
     * search-suggestions only returns enough to identify the deck
     * (titles, deckId, cover) — the actual computed difficulty numbers
     * only live on the per-deck detail endpoint, confirmed from a real
     * response: { data: { mainDeck: { difficulty, difficultyRaw, ... } } }.
     */
    async function getJitenDeckDetail(deckId) {
        const url = `${CONFIG.JITEN_API}/api/media-deck/${deckId}/detail?offset=0`;
        try {
            const text = await request(url);
            const parsed = JSON.parse(text);
            return parsed?.data?.mainDeck || null;
        } catch (error) {
            console.debug('[AP-JP] Jiten detail request failed:', error);
            return null;
        }
    }

    /*
     * Same reasoning as JPDB: Jiten is a Japanese-learner tool and likely
     * indexes media by their Japanese/romaji title, not AP's English
     * localization, so try alt titles first and fall back to the
     * English title for shows that are already Japanese-named.
     */
    async function getJiten(item) {
        const mediaType = CONTEXT.type;
        const cacheTitle = item.title;
        const cached = getCached(mediaType, cacheTitle);
        if (cached && 'jiten' in cached) {
            return cached.jiten;
        }

        const jitenMediaType = CONFIG.JITEN_MEDIA_TYPE[mediaType];
        const queries = [...(item.altTitles || []), item.title];
        let best = null;
        let bestScore = 0;

        for (const query of queries) {
            const results = await searchJiten(query, jitenMediaType);
            for (const result of results) {
                const score = bestJitenSimilarity(query, result);
                if (score > bestScore) {
                    bestScore = score;
                    best = result;
                }
            }
            if (bestScore >= CONFIG.MATCH_THRESHOLD) {
                break;
            }
        }

        let final = null;
        if (best && bestScore >= CONFIG.MATCH_THRESHOLD) {
            const detail = best.deckId
                ? await getJitenDeckDetail(best.deckId)
                : null;
            final = {
                ...best,
                ...(detail || {}),
                matchScore: bestScore
            };
        }

        setCached(mediaType, cacheTitle, { jiten: final });
        return final;
    }

    /*
     * DIAGNOSTIC: probes Jiten's search-suggestions endpoint for a known
     * title and reports what comes back, filtered/unfiltered, so media-
     * type mismatches or API shape changes are visible at a glance.
     */
    async function probeJitenAPI() {
        const output = document.getElementById('ap-jp-debug');
        if (!output) {
            return;
        }
        output.style.display = 'block';
        output.textContent = 'Probing Jiten API…';

        const sampleItem = animeItems[0] || {
            title: CONTEXT.type === 'manga' ? 'Yotsuba&!' : 'Bocchi the Rock!',
            altTitles: []
        };
        const jitenMediaType = CONFIG.JITEN_MEDIA_TYPE[CONTEXT.type];
        const queries = [...(sampleItem.altTitles || []), sampleItem.title];

        const report = {
            mediaType: CONTEXT.type,
            expectedJitenMediaType: jitenMediaType,
            sampleTitle: sampleItem.title,
            attempts: []
        };

        for (const query of queries) {
            const url =
                `${CONFIG.JITEN_API}/api/media-deck/search-suggestions` +
                `?query=${encodeURIComponent(query)}`;
            try {
                const text = await requestJSON({ method: 'GET', url });
                let parsed = null;
                let parseError = null;
                try {
                    parsed = JSON.parse(text);
                } catch (error) {
                    parseError = error.message;
                }
                const all = parsed?.suggestions || [];
                const filtered = all.filter(r => r?.mediaType === jitenMediaType);

                report.attempts.push({
                    query,
                    url,
                    status: 'ok',
                    responseLength: text.length,
                    jsonParseError: parseError,
                    totalSuggestions: all.length,
                    matchingMediaType: filtered.map(r => ({
                        deckId: r.deckId,
                        romajiTitle: r.romajiTitle,
                        englishTitle: r.englishTitle,
                        mediaType: r.mediaType,
                        score: bestJitenSimilarity(query, r)
                    })),
                    allSuggestions: all.map(r => ({
                        deckId: r.deckId,
                        romajiTitle: r.romajiTitle,
                        mediaType: r.mediaType
                    }))
                });
            } catch (error) {
                report.attempts.push({
                    query,
                    url,
                    status: 'error',
                    error: error.message
                });
            }
        }

        output.textContent = JSON.stringify(report, null, 2);
        console.log('[AP-JP] Jiten API probe report:', report);
    }

    /********************************************************************
     * BADGES (list rows)
     ********************************************************************/

    /*
     * Where to attach the badge span. The row/card element (item.card)
     * may be something like a <tr>, which cannot legally hold arbitrary
     * children — only <td>/<th>. So badges get attached next to the
     * anchor itself: its containing <td> if there is one, otherwise its
     * immediate parent, otherwise the card as a last resort.
     */
    function getBadgeContainer(item) {
        return item.link.closest('td') || item.link.parentElement || item.card;
    }

    function renderBadge(item) {
        const container = getBadgeContainer(item);
        container.querySelector('.ap-jp-badges')?.remove();

        const wrapper = document.createElement('span');
        wrapper.className = 'ap-jp-badges';

        const jpdb = item.result?.jpdb;
        if (CONTEXT.type === 'anime') {
            if (jpdb) {
                const badge = document.createElement('span');
                badge.className = 'ap-jp-badge';
                badge.textContent = `JPDB ${jpdb.average}`;
                badge.title = `${jpdb.title} (match ${Math.round(jpdb.matchScore * 100)}%)`;
                wrapper.appendChild(badge);
            } else if (jpdbLoaded) {
                const badge = document.createElement('span');
                badge.className = 'ap-jp-badge unknown';
                badge.textContent = 'JPDB —';
                wrapper.appendChild(badge);
            }
        }

        if (item.result?.jiten) {
            const badge = document.createElement('span');
            badge.className = 'ap-jp-badge';
            const jitenValue =
                typeof item.result.jiten.difficultyRaw === 'number'
                    ? item.result.jiten.difficultyRaw.toFixed(1)
                    : item.result.jiten.difficulty;
            badge.textContent =
                jitenValue != null ? `Jiten ${jitenValue}` : 'Jiten ✓';
            const jitenTitle = getJitenTitles(item.result.jiten)[0];
            if (jitenTitle) {
                badge.title =
                    `${jitenTitle} ` +
                    `(match ${Math.round(item.result.jiten.matchScore * 100)}%)`;
            }
            wrapper.appendChild(badge);
        } else if (jitenLoaded) {
            /*
             * Only show "no match" once Jiten has actually been checked —
             * before that, showing nothing here correctly reflects that
             * we haven't looked yet (Jiten loads lazily).
             */
            const badge = document.createElement('span');
            badge.className = 'ap-jp-badge unknown';
            badge.textContent = 'Jiten —';
            wrapper.appendChild(badge);
        }

        container.appendChild(wrapper);
    }

    /********************************************************************
     * LOAD ALL DIFFICULTY DATA (list pages)
     ********************************************************************/

    async function runWorkers(items, concurrency, worker) {
        const queue = [...items];

        async function run() {
            while (queue.length) {
                const item = queue.shift();
                if (!item) {
                    return;
                }
                await worker(item);
            }
        }

        const workers = Math.min(concurrency, queue.length);
        await Promise.all(Array.from({ length: workers }, run));
    }

    async function loadDifficulty() {
        if (!animeItems.length) {
            setStatus('No list detected. Click Debug.');
            return;
        }

        const button = document.querySelector(
            '#ap-jp-difficulty [data-action="load"]'
        );
        if (button) {
            button.disabled = true;
        }

        if (CONTEXT.type === 'anime') {
            /*
             * Anime: JPDB first (fast, has real difficulty numbers),
             * Jiten stays lazy until a Jiten/Combined sort is clicked.
             */
            const uncachedJPDB = animeItems.filter(item => {
                const cached = getCached('anime', item.title);
                return !(cached && 'jpdb' in cached);
            });

            let completed = 0;
            setStatus(
                `Loading JPDB… ` +
                `${uncachedJPDB.length === 0 ? 'cached' : '0/' + uncachedJPDB.length}`
            );

            await runWorkers(uncachedJPDB, CONFIG.JPDB_CONCURRENCY, async item => {
                try {
                    await getJPDB(item);
                } catch (error) {
                    console.warn('[AP-JP]', error);
                    setCached('anime', item.title, { jpdb: null });
                }
                completed++;
                setStatus(`Loading JPDB… ${completed}/${uncachedJPDB.length}`);
            });

            for (const item of animeItems) {
                const cached = getCached('anime', item.title);
                item.result = {
                    jpdb: cached?.jpdb ?? null,
                    jiten: cached?.jiten ?? null
                };
                renderBadge(item);
            }

            jpdbLoaded = true;

            document
                .querySelectorAll('#ap-jp-difficulty [data-sort]')
                .forEach(button => (button.disabled = false));

            setStatus(
                `JPDB ready — ` +
                `${animeItems.filter(x => x.result?.jpdb).length}/${animeItems.length} matched.`
            );
        } else {
            /*
             * Manga: no JPDB data exists at all, so Jiten is the primary
             * (and only) source — load it eagerly instead of lazily.
             */
            await loadJiten();

            document
                .querySelectorAll('#ap-jp-difficulty [data-sort]')
                .forEach(button => (button.disabled = false));
        }
    }

    async function loadJiten() {
        if (jitenLoaded) {
            return;
        }

        const mediaType = CONTEXT.type;
        const uncached = animeItems.filter(item => {
            const cached = getCached(mediaType, item.title);
            return !(cached && 'jiten' in cached);
        });

        let completed = 0;
        setStatus(
            `Loading Jiten… ` +
            `${uncached.length === 0 ? 'cached' : '0/' + uncached.length}`
        );

        await runWorkers(uncached, CONFIG.JITEN_CONCURRENCY, async item => {
            try {
                await getJiten(item);
            } catch (error) {
                console.warn('[AP-JP]', error);
                setCached(mediaType, item.title, { jiten: null });
            }
            completed++;
            setStatus(`Loading Jiten… ${completed}/${uncached.length}`);
        });

        for (const item of animeItems) {
            const cached = getCached(mediaType, item.title);
            item.result = {
                ...(item.result || {}),
                jiten: cached?.jiten ?? null
            };
        }

        jitenLoaded = true;

        for (const item of animeItems) {
            renderBadge(item);
        }

        setStatus(
            `Jiten ready — ` +
            `${animeItems.filter(x => x.result?.jiten).length}/${animeItems.length} matched.`
        );
    }

    /********************************************************************
     * SORTING
     ********************************************************************/

    function jpdbScore(item) {
        return item.result?.jpdb?.average ?? null;
    }

    function jitenScore(item) {
        const j = item.result?.jiten;
        if (!j) {
            return null;
        }
        /*
         * Confirmed field names from Jiten's actual API: `difficultyRaw`
         * is the finer-grained decimal score (e.g. 3.52); `difficulty`
         * is the rounded bucket (e.g. 3) that the site's UI labels as
         * words like "Hard". Prefer the raw value for sorting since it
         * gives better tie-breaking resolution.
         */
        if (typeof j.difficultyRaw === 'number') {
            return j.difficultyRaw;
        }
        if (typeof j.difficulty === 'number') {
            return j.difficulty;
        }
        return null;
    }

    function combinedScore(item) {
        const jpdb = jpdbScore(item);
        const jiten = jitenScore(item);
        if (jpdb == null && jiten == null) {
            return null;
        }
        if (jpdb == null) {
            return jiten;
        }
        if (jiten == null) {
            return jpdb;
        }
        return jpdb * 0.7 + jiten * 0.3;
    }

    async function sortItems(mode) {
        if (mode === 'jiten-asc' || mode === 'jiten-desc' || mode === 'combined-asc') {
            await loadJiten();
        }

        if (!listParent || animeItems.length < 2) {
            setStatus('List is unavailable.');
            return;
        }

        /*
         * HARD SAFETY CHECK.
         *
         * We only move the exact row/card elements we detected.
         *
         * If Anime-Planet has changed the DOM, abort.
         */
        const safe = animeItems.every(
            item => item.card.parentElement === listParent
        );
        if (!safe) {
            setStatus('Anime-Planet changed the list DOM. Refusing to sort.');
            return;
        }

        if (mode === 'original') {
            animeItems.sort((a, b) => a.originalIndex - b.originalIndex);
        } else {
            let scoreFunction;
            if (mode.startsWith('jpdb')) {
                scoreFunction = jpdbScore;
            } else if (mode.startsWith('jiten')) {
                scoreFunction = jitenScore;
            } else {
                scoreFunction = combinedScore;
            }

            const descending = mode.endsWith('-desc');

            animeItems.sort((a, b) => {
                const aa = scoreFunction(a);
                const bb = scoreFunction(b);

                /*
                 * Unknown values always go last.
                 */
                if (aa == null && bb == null) {
                    return a.originalIndex - b.originalIndex;
                }
                if (aa == null) {
                    return 1;
                }
                if (bb == null) {
                    return -1;
                }
                if (aa === bb) {
                    return a.originalIndex - b.originalIndex;
                }
                return descending ? bb - aa : aa - bb;
            });
        }

        /*
         * Move ONLY the row/card elements.
         *
         * DocumentFragment avoids repeated layout/reflow.
         */
        const fragment = document.createDocumentFragment();
        for (const item of animeItems) {
            fragment.appendChild(item.card);
        }
        listParent.appendChild(fragment);

        /*
         * Mark active button.
         */
        document
            .querySelectorAll('#ap-jp-difficulty [data-sort]')
            .forEach(button => {
                button.classList.toggle('active', button.dataset.sort === mode);
            });

        setStatus(
            mode === 'original' ? 'Original order restored.' : `Sorted by ${mode}.`
        );
    }

    /********************************************************************
     * DETAIL PAGE (single anime/manga)
     ********************************************************************/

    function getDetailTitleAndAltTitles() {
        const h1 = document.querySelector('h1');
        const title = h1?.textContent?.replace(/\s+/g, ' ').trim();
        if (!title) {
            return null;
        }

        const akaEl = document.querySelector('h2.aka');
        let altTitles = [];
        if (akaEl) {
            const text = akaEl.textContent.replace(/\s+/g, ' ').trim();
            altTitles = text
                .replace(/^Alt titles?:\s*/i, '')
                .split(',')
                .map(part => part.trim())
                .filter(Boolean);
        }

        return { h1, akaEl, title, altTitles };
    }

    function jitenBadgeHTML(jiten) {
        const value =
            typeof jiten.difficultyRaw === 'number'
                ? jiten.difficultyRaw.toFixed(1)
                : jiten.difficulty;
        const href = jiten.deckId
            ? `https://jiten.moe/decks/media/${jiten.deckId}/stats`
            : 'https://jiten.moe';
        const label = value != null ? `Jiten ${value}` : 'Jiten ✓';
        return `<a class="ap-jp-badge" href="${href}" target="_blank" rel="noopener">${label}</a>`;
    }

    function jpdbBadgeHTML(jpdb) {
        const href = `${CONFIG.JPDB_URL}?q=${encodeURIComponent(jpdb.title)}`;
        return `<a class="ap-jp-badge" href="${href}" target="_blank" rel="noopener">JPDB ${jpdb.average}/100</a>`;
    }

    async function runDetailPage() {
        const info = getDetailTitleAndAltTitles();
        if (!info) {
            return;
        }

        const badge = document.createElement('div');
        badge.id = 'ap-jp-detail-badge';
        badge.textContent = 'Checking Japanese difficulty…';
        (info.akaEl || info.h1).insertAdjacentElement('afterend', badge);

        const item = { title: info.title, altTitles: info.altTitles };
        const parts = [];

        if (CONTEXT.type === 'anime') {
            try {
                const jpdb = await getJPDB(item);
                if (jpdb) {
                    parts.push(jpdbBadgeHTML(jpdb));
                }
            } catch (error) {
                console.warn('[AP-JP]', error);
            }
        }

        try {
            const jiten = await getJiten(item);
            if (jiten) {
                parts.push(jitenBadgeHTML(jiten));
            }
        } catch (error) {
            console.warn('[AP-JP]', error);
        }

        if (parts.length) {
            badge.innerHTML = 'Japanese difficulty: ' + parts.join(' ');
        } else {
            badge.textContent = 'Japanese difficulty: no match found.';
            badge.classList.add('ap-jp-badge-empty');
        }
    }

    /********************************************************************
     * STARTUP
     ********************************************************************/

    function startup() {
        if (!CONTEXT) {
            return;
        }

        migrateCache();
        installCSS();

        if (CONTEXT.kind === 'detail') {
            runDetailPage();
            return;
        }

        createUI();
        startDetection();
    }

    startup();
})();
