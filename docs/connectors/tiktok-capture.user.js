// ==UserScript==
// @name         Контур — TikTok Studio capture
// @namespace    kontur.rosta
// @version      3.2
// @description  Пассивно сохраняет ответы TikTok Studio и автоматически последовательно открывает аналитику публикаций с фиксированным интервалом и стопом при защитной проверке.
// @match        https://www.tiktok.com/tiktokstudio/*
// @run-at       document-start
// @grant        unsafeWindow
// @grant        GM_setValue
// @grant        GM_getValue
// @grant        GM_deleteValue
// @grant        GM_xmlhttpRequest
// @connect      *
// ==/UserScript==

/*
 Версия 3.2 не подделывает запросы, подписи, заголовки или поведение пользователя.
 Она только читает ответы, которые загрузил TikTok Studio, и открывает штатные
 страницы аналитики в одной вкладке. Между страницами — не менее 15 секунд.
 При 401/403/429, CAPTCHA, challenge или выходе из аккаунта обход немедленно
 останавливается.

 Перед каждым сбором нажми «Новый сбор», затем вручную промотай «Публикации» до
 конца. «Проверить» покажет размер каталога. Сервер примет только один завершённый
 пакет v3: весь каталог + явно указанные закрепы должны быть полностью обойдены.
*/

(function () {
  'use strict';

  const W = typeof unsafeWindow !== 'undefined' ? unsafeWindow : window;
  const SCRIPT_VERSION = '3.2';
  const PAGE_DELAY_MS = 15000;
  const OWNER_TTL_MS = 60000;
  const TABS = ['', 'viewers', 'engagement'];
  const TAB_ID_KEY = 'kontur_tiktok_tab_id';
  const TAB_ID = sessionStorage.getItem(TAB_ID_KEY) || makeId();
  sessionStorage.setItem(TAB_ID_KEY, TAB_ID);

  const K = {
    schema: 'k3_schema', cap: 'k3_cap', queue: 'k3_queue', mode: 'k3_mode',
    ep: 'k_ep', tok: 'k_tok', hb: 'k_hb', pins: 'k3_pins', batch: 'k3_batch',
    expected: 'k3_expected', visited: 'k3_visited', dom: 'k3_dom_ids',
    owner: 'k3_owner', safety: 'k3_safety', overview: 'k3_overview',
    overviewName: 'k3_overview_name', year: 'k3_overview_year', notice: 'k3_notice',
  };

  let el;

  function makeId() {
    if (W.crypto && typeof W.crypto.randomUUID === 'function') return W.crypto.randomUUID();
    return Date.now().toString(36) + '-' + Math.random().toString(36).slice(2);
  }

  function getJSON(key, fallback) {
    try { return JSON.parse(GM_getValue(key, '')) ?? fallback; } catch (e) { return fallback; }
  }

  function setJSON(key, value) { GM_setValue(key, JSON.stringify(value)); }

  function initState() {
    if (GM_getValue(K.schema, '') === SCRIPT_VERSION) return;
    [K.cap, K.queue, K.mode, K.batch, K.expected, K.visited, K.dom,
      K.owner, K.safety, K.overview, K.overviewName, K.year].forEach(GM_deleteValue);
    GM_setValue(K.schema, SCRIPT_VERSION);
  }

  function ensureBatch() {
    let id = GM_getValue(K.batch, '');
    if (!id) {
      id = makeId();
      GM_setValue(K.batch, id);
      setJSON(K.cap, {});
      setJSON(K.queue, []);
      setJSON(K.expected, []);
      setJSON(K.visited, {});
      setJSON(K.dom, []);
      GM_setValue(K.mode, 'idle');
    }
    return id;
  }

  const WANT = (url) =>
    /\/aweme\/v2\/data\/insight|comment\/v1\/get_key_words|ai_comment\/analytics_overview|creator\/manage\/item_list/.test(url);
  const isItemList = (url) => /creator\/manage\/item_list/.test(url);
  const isInsight = (url) => /\/aweme\/v2\/data\/insight/.test(url);

  function awemeIds(url, json) {
    const ids = new Set();
    const match = /type_requests=([^&]+)/.exec(url || '');
    if (match) {
      try {
        const requests = JSON.parse(decodeURIComponent(match[1]));
        requests.forEach((request) => {
          if (request && request.aweme_id) ids.add(String(request.aweme_id));
        });
      } catch (e) {
        const decoded = decodeURIComponent(match[1]);
        for (const found of decoded.matchAll(/"aweme_id"\s*:\s*"(\d+)"/g)) ids.add(found[1]);
      }
    }
    const queryId = /[?&](?:vid|item_id|aweme_id)=(\d+)/.exec(url || '');
    if (queryId) ids.add(queryId[1]);
    if (json && json.video_info && json.video_info.aweme_id) {
      ids.add(String(json.video_info.aweme_id));
    }
    return [...ids];
  }

  function hash(value) {
    let result = 5381;
    for (let i = 0; i < value.length; i++) result = ((result << 5) + result + value.charCodeAt(i)) | 0;
    return (result >>> 0).toString(36);
  }

  function parseIds(text) {
    const ids = new Set();
    let rejected = 0;
    let shortlink = false;
    for (let token of String(text || '').split(/[\s,;]+/)) {
      token = token.trim();
      if (!token) continue;
      const match = /\/(?:video|photo|analytics)\/(\d{6,})/.exec(token);
      if (match) { ids.add(match[1]); continue; }
      if (/^\d{6,}$/.test(token)) { ids.add(token); continue; }
      if (/(?:vm|vt)\.tiktok\.com|\/t\//i.test(token)) shortlink = true;
      rejected++;
    }
    return { ids: [...ids], rejected, shortlink };
  }

  function status(message) {
    if (el) el.querySelector('#k-st').textContent = message;
  }

  function record(url, json) {
    if (!WANT(url) || !json || typeof json !== 'object') return;
    ensureBatch();
    const cap = getJSON(K.cap, {});
    let key;
    if (isItemList(url)) {
      const ids = (json.item_list || []).map((item) => item && item.item_id).filter(Boolean).map(String);
      key = 'itemlist|' + ids.length + '|' + hash(ids.join(','));
      const pins = new Set(getJSON(K.pins, []));
      (json.item_list || []).forEach((item) => {
        if (item && item.item_id && (item.is_top || item.pin_status || item.pinned)) {
          pins.add(String(item.item_id));
        }
      });
      setJSON(K.pins, [...pins]);
    } else {
      const ids = awemeIds(url, json);
      key = (ids.join(',') || '_') + '|' + url.split('?')[0] + '|' + hash(url);
    }
    cap[key] = { url, json };
    setJSON(K.cap, cap);
    paint();
  }

  function catalogState() {
    const ids = new Set();
    let pages = 0;
    let complete = false;
    Object.values(getJSON(K.cap, {})).forEach((entry) => {
      if (!entry || !isItemList(entry.url || '') || !entry.json || !Array.isArray(entry.json.item_list)) return;
      pages++;
      entry.json.item_list.forEach((item) => {
        if (item && item.item_id) ids.add(String(item.item_id));
      });
      const hasMore = entry.json.has_more ?? entry.json.hasMore;
      if (hasMore === false || hasMore === 0 || hasMore === '0') complete = true;
      const requested = /[?&]count=(\d+)/.exec(entry.url || '');
      if (hasMore == null && requested && entry.json.item_list.length < Number(requested[1])) complete = true;
    });
    return { ids, pages, complete };
  }

  function catalogIds() { return catalogState().ids; }

  function insightIds() {
    const ids = new Set();
    Object.values(getJSON(K.cap, {})).forEach((entry) => {
      if (!entry || !isInsight(entry.url || '') || !entry.json || !entry.json.video_info) return;
      awemeIds(entry.url, entry.json).forEach((id) => ids.add(id));
    });
    return ids;
  }

  function savePins() {
    const input = el && el.querySelector('#k-pins');
    const parsed = parseIds(input ? input.value : '');
    if (parsed.ids.length || !(input && input.value.trim())) setJSON(K.pins, parsed.ids);
    return parsed;
  }

  function scanDomIds() {
    const ids = new Set(getJSON(K.dom, []));
    document.querySelectorAll('a[href*="/video/"],a[href*="/photo/"],a[href*="/analytics/"]').forEach((link) => {
      const match = /\/(?:video|photo|analytics)\/(\d{6,})/.exec(link.getAttribute('href') || '');
      if (match) ids.add(match[1]);
    });
    setJSON(K.dom, [...ids]);
    return ids;
  }

  function discoveredIds() {
    const catalog = catalogIds();
    const manualPins = new Set(getJSON(K.pins, []).map(String));
    return new Set([...getJSON(K.dom, [])].map(String).filter((id) => !catalog.has(id) && !manualPins.has(id)));
  }

  function allIds() {
    const ids = catalogIds();
    getJSON(K.pins, []).forEach((id) => ids.add(String(id)));
    getJSON(K.dom, []).forEach((id) => ids.add(String(id)));
    return ids;
  }

  function stepUrl(step) {
    return 'https://www.tiktok.com/tiktokstudio/analytics/' + step.id + (step.tab ? '/' + step.tab : '');
  }

  function ownerLease() { return getJSON(K.owner, null); }

  function claimOwner() {
    const current = ownerLease();
    const now = Date.now();
    if (current && current.tab !== TAB_ID && current.expires > now) return false;
    setJSON(K.owner, { tab: TAB_ID, expires: now + OWNER_TTL_MS });
    return true;
  }

  function releaseOwner() {
    const current = ownerLease();
    if (current && current.tab === TAB_ID) GM_deleteValue(K.owner);
  }

  function safetyStop(reason) {
    if (GM_getValue(K.safety, '')) return;
    GM_setValue(K.safety, reason);
    GM_setValue(K.mode, 'stopped');
    releaseOwner();
    status('СТОП: ' + reason + '. Ничего не продолжай, пока не проверишь аккаунт.');
    paint();
  }

  function checkPageSafety() {
    if (/\/(?:login|challenge|captcha)(?:\/|\?|$)/i.test(location.href)) {
      safetyStop('TikTok открыл страницу входа или проверки');
      return false;
    }
    if (document.querySelector('iframe[src*="captcha" i], [id*="captcha" i], [class*="captcha" i], [data-e2e*="captcha" i]')) {
      safetyStop('на странице обнаружена CAPTCHA');
      return false;
    }
    return !GM_getValue(K.safety, '');
  }

  const originalFetch = W.fetch;
  W.fetch = async function (...args) {
    const response = await originalFetch.apply(this, args);
    try {
      const url = typeof args[0] === 'string' ? args[0] : (args[0] && args[0].url) || '';
      if ([401, 403, 429].includes(response.status)) safetyStop('TikTok ответил HTTP ' + response.status);
      if (WANT(url)) record(url, JSON.parse(await response.clone().text()));
    } catch (e) {}
    return response;
  };

  const originalOpen = W.XMLHttpRequest.prototype.open;
  const originalSend = W.XMLHttpRequest.prototype.send;
  W.XMLHttpRequest.prototype.open = function (method, url) {
    this.__konturUrl = url;
    return originalOpen.apply(this, arguments);
  };
  W.XMLHttpRequest.prototype.send = function () {
    this.addEventListener('load', () => {
      try {
        if ([401, 403, 429].includes(this.status)) safetyStop('TikTok ответил HTTP ' + this.status);
        if (WANT(this.__konturUrl || '')) record(this.__konturUrl, JSON.parse(this.responseText));
      } catch (e) {}
    });
    return originalSend.apply(this, arguments);
  };

  function currentStep() {
    const match = /\/tiktokstudio\/analytics\/(\d{6,})(?:\/(viewers|engagement))?/.exec(location.pathname);
    return match ? { id: match[1], tab: match[2] || '' } : null;
  }

  function markCurrentVisited() {
    const step = currentStep();
    if (!step) return;
    const visited = getJSON(K.visited, {});
    const tabs = new Set(visited[step.id] || []);
    tabs.add(step.tab);
    visited[step.id] = [...tabs];
    setJSON(K.visited, visited);
  }

  function buildMissingQueue(ids) {
    const visited = getJSON(K.visited, {});
    const queue = [];
    ids.forEach((id) => {
      const done = new Set(visited[id] || []);
      TABS.forEach((tab) => { if (!done.has(tab)) queue.push({ id, tab }); });
    });
    return queue;
  }

  function advance() {
    if (GM_getValue(K.mode, 'idle') !== 'walking' || !checkPageSafety()) return;
    if (!claimOwner()) {
      status('Пауза: обход уже выполняется в другой вкладке.');
      paint();
      return;
    }
    const queue = getJSON(K.queue, []);
    if (!queue.length) { finishWalk(); return; }
    const next = queue.shift();
    setJSON(K.queue, queue);
    setJSON(K.owner, { tab: TAB_ID, expires: Date.now() + OWNER_TTL_MS });
    paint();
    location.href = stepUrl(next);
  }

  function completePageAndAdvance() {
    if (!checkPageSafety() || GM_getValue(K.mode, 'idle') !== 'walking') return;
    if (!claimOwner()) {
      status('Пауза: владельцем обхода стала другая вкладка.');
      paint();
      return;
    }
    markCurrentVisited();
    advance();
  }

  function startWalk() {
    if (!checkPageSafety()) return;
    ensureBatch();
    scanDomIds();
    const parsed = savePins();
    if (parsed.shortlink) {
      status('Короткие ссылки не поддержаны: открой их и вставь полный URL /video/<id>.');
      return;
    }
    const catalog = catalogState();
    const ids = [...allIds()];
    if (!ids.length) {
      status('Видео не найдены. Промотай «Публикации» до конца и нажми «Проверить».');
      return;
    }
    if (catalog.ids.size && !catalog.complete) {
      status('Старт заблокирован: поймано ' + catalog.pages + ' страниц каталога, но последняя страница не получена. Нажми «Новый сбор», дождись перезагрузки и промотай список один раз до конца.');
      return;
    }
    if (!claimOwner()) {
      status('Обход уже запущен в другой вкладке. Закрой её или нажми там «Пауза».');
      return;
    }
    const queue = buildMissingQueue(ids);
    setJSON(K.expected, ids);
    setJSON(K.queue, queue);
    if (!queue.length) { finishWalk(); return; }
    GM_setValue(K.mode, 'walking');
    status('Начинаю последовательный обход: ' + ids.length + ' публикаций.');
    paint();
    advance();
  }

  function resumeWalk() {
    if (!checkPageSafety()) return;
    if (!getJSON(K.queue, []).length) { startWalk(); return; }
    if (!claimOwner()) {
      status('Обход уже выполняется в другой вкладке.');
      return;
    }
    GM_setValue(K.mode, 'walking');
    status('Обход продолжен.');
    paint();
    advance();
  }

  function finishWalk() {
    const catalog = catalogState();
    if (catalog.ids.size && !catalog.complete) {
      GM_setValue(K.mode, 'paused');
      releaseOwner();
      status('Сбор остановлен: нет подтверждения последней страницы каталога. Начни новый сбор со страницы «Публикации».');
      paint();
      return;
    }
    const expected = new Set(getJSON(K.expected, []));
    const current = allIds();
    current.forEach((id) => expected.add(id));
    setJSON(K.expected, [...expected]);
    const missingPages = buildMissingQueue([...expected]);
    const insights = insightIds();
    const missingInsights = [...expected].filter((id) => !insights.has(id));
    if (missingPages.length || missingInsights.length) {
      setJSON(K.queue, missingPages);
      GM_setValue(K.mode, 'paused');
      releaseOwner();
      status('Сбор неполный: страниц осталось ' + missingPages.length + ', ответов аналитики — ' + missingInsights.length + '. Нажми «Продолжить».');
      paint();
      return;
    }
    GM_setValue(K.mode, 'ready');
    releaseOwner();
    status('Все публикации собраны. Отправляю один полный пакет…');
    paint();
    upload(true);
  }

  function payload() {
    const capture = Object.values(getJSON(K.cap, {}));
    const catalog = catalogIds();
    const insights = insightIds();
    const expected = allIds();
    const body = {
      capture,
      pinned_ids: getJSON(K.pins, []),
      discovered_ids: [...discoveredIds()],
      batch_id: GM_getValue(K.batch, ''),
      script_version: SCRIPT_VERSION,
      expected_videos: expected.size,
      catalog_videos: catalog.size,
      insight_videos: insights.size,
      complete: true,
    };
    const overview = GM_getValue(K.overview, '');
    if (overview) {
      body.overview = overview;
      body.year = Number(GM_getValue(K.year, new Date().getFullYear()));
    }
    return body;
  }

  function validateBeforeUpload(body) {
    if (GM_getValue(K.safety, '')) return 'активен защитный стоп';
    if (!body.batch_id) return 'нет batch_id — начни новый сбор';
    if (!body.capture.length || !body.expected_videos) return 'буфер пуст';
    const catalog = catalogState();
    if (catalog.ids.size && !catalog.complete) return 'нет последней страницы каталога';
    if (getJSON(K.queue, []).length || GM_getValue(K.mode, 'idle') === 'walking') return 'обход ещё не завершён';
    if (body.insight_videos !== body.expected_videos) {
      return 'полная аналитика есть только для ' + body.insight_videos + ' из ' + body.expected_videos;
    }
    const catalogIdsNow = catalogIds();
    const outsideCatalog = new Set([
      ...getJSON(K.pins, []).map(String),
      ...(body.discovered_ids || []).map(String),
    ].filter((id) => !catalogIdsNow.has(id)));
    if (body.catalog_videos + outsideCatalog.size !== body.expected_videos) {
      return 'каталог и найденные на странице публикации не покрывают весь список';
    }
    if (buildMissingQueue([...allIds()]).length) return 'не все три страницы каждой публикации пройдены';
    return '';
  }

  function upload(auto) {
    const endpointInput = el && el.querySelector('#k-ep');
    const tokenInput = el && el.querySelector('#k-tok');
    if (endpointInput && endpointInput.value.trim()) GM_setValue(K.ep, endpointInput.value.trim());
    if (tokenInput && tokenInput.value.trim()) GM_setValue(K.tok, tokenInput.value.trim());
    const endpoint = GM_getValue(K.ep, '');
    const token = GM_getValue(K.tok, '');
    if (!endpoint || !token) { status('Впиши Endpoint и Token.'); return; }
    const bodyObject = payload();
    const problem = validateBeforeUpload(bodyObject);
    if (problem) { status('Не отправлено: ' + problem + '.'); return; }
    const body = JSON.stringify(bodyObject);
    const megabytes = (body.length / 1048576).toFixed(1);
    if (body.length > 48 * 1048576) {
      status('Пакет ' + megabytes + ' МБ превышает безопасный лимит 48 МБ. Скачай его для ручной проверки.');
      return;
    }
    status('Отправляю один полный пакет (' + bodyObject.expected_videos + ' видео, ' + megabytes + ' МБ)…');
    GM_xmlhttpRequest({
      method: 'POST',
      url: endpoint,
      headers: { 'Content-Type': 'application/json', 'X-Kontur-Token': token },
      data: body,
      timeout: 120000,
      onload: (response) => {
        if (response.status === 200) {
          let count = bodyObject.expected_videos;
          try {
            const parsed = JSON.parse(response.responseText);
            if (parsed && parsed.stats && parsed.stats.videos) count = parsed.stats.videos;
          } catch (e) {}
          GM_setValue(K.hb, new Date().toISOString());
          [K.cap, K.queue, K.expected, K.visited, K.dom, K.batch, K.overview,
            K.overviewName, K.year].forEach(GM_deleteValue);
          GM_setValue(K.mode, 'idle');
          status('Залито ✓ · видео: ' + count);
          paint();
        } else {
          status('Сервер отклонил пакет (' + response.status + '): ' + (response.responseText || '').slice(0, 220) + '. Буфер сохранён.');
          paint();
        }
      },
      onerror: () => { status('Ошибка сети. Буфер сохранён.'); paint(); },
      ontimeout: () => { status('Таймаут 120 секунд. Буфер сохранён.'); paint(); },
    });
  }

  function download() {
    const body = payload();
    const blob = new Blob([JSON.stringify(body)], { type: 'application/json' });
    const link = document.createElement('a');
    link.href = URL.createObjectURL(blob);
    link.download = 'tiktok_batch_' + new Date().toISOString().slice(0, 10) + '.json';
    link.click();
    setTimeout(() => URL.revokeObjectURL(link.href), 1000);
  }

  function newBatch() {
    releaseOwner();
    [K.cap, K.queue, K.expected, K.visited, K.dom, K.batch, K.safety,
      K.overview, K.overviewName, K.year].forEach(GM_deleteValue);
    GM_setValue(K.mode, 'idle');
    ensureBatch();
    GM_setValue(K.notice, 'Новый пустой сбор. Дождись загрузки «Публикаций» и промотай список один раз до конца.');
    status('Очищаю кэш сбора и перезагружаю страницу…');
    paint();
    setTimeout(() => location.reload(), 250);
  }

  function stopWalk() {
    GM_setValue(K.mode, 'paused');
    releaseOwner();
    status('Обход поставлен на паузу. Буфер и очередь сохранены.');
    paint();
  }

  function readOverview(file) {
    if (!file) return;
    const reader = new FileReader();
    reader.onload = () => {
      GM_setValue(K.overview, String(reader.result || ''));
      GM_setValue(K.overviewName, file.name);
      const match = /(20\d{2})[-_]/.exec(file.name);
      if (match) GM_setValue(K.year, match[1]);
      const yearInput = el && el.querySelector('#k-year');
      if (yearInput) yearInput.value = GM_getValue(K.year, new Date().getFullYear());
      status('Overview.csv добавлен: ' + file.name);
      paint();
    };
    reader.readAsText(file);
  }

  function paint() {
    if (!el) return;
    const catalog = catalogState();
    const insights = insightIds();
    const discovered = discoveredIds();
    const total = allIds();
    const queue = getJSON(K.queue, []);
    const mode = GM_getValue(K.mode, 'idle');
    const heartbeat = GM_getValue(K.hb, '—');
    const overview = GM_getValue(K.overviewName, 'нет');
    el.querySelector('#k-n').textContent =
      'каталог ' + catalog.ids.size + ' · страниц ' + catalog.pages + ' · конец ' + (catalog.complete ? 'да' : 'нет') +
      ' · вне каталога ' + discovered.size + ' · всего ' + total.size + ' · аналитика ' + insights.size +
      ' · очередь ' + queue.length + ' · режим ' + mode + ' · Overview ' + overview +
      ' · залито ' + heartbeat;
  }

  function mountUI() {
    el = document.createElement('div');
    el.style.cssText = 'position:fixed;right:14px;bottom:14px;z-index:2147483647;background:#111;color:#fff;font:12px/1.4 -apple-system,sans-serif;padding:10px;border-radius:10px;box-shadow:0 4px 16px rgba(0,0,0,.35);width:340px;opacity:.96';
    el.innerHTML =
      '<div style="font-weight:600;margin-bottom:6px">Контур · TikTok v3.2</div>' +
      '<div id="k-n" style="color:#7fd;margin-bottom:6px;word-break:break-word">—</div>' +
      '<input id="k-ep" placeholder="Endpoint /ingest/tiktok" style="box-sizing:border-box;width:100%;margin-bottom:4px;padding:5px;border-radius:5px;border:0">' +
      '<input id="k-tok" type="password" placeholder="Token" style="box-sizing:border-box;width:100%;margin-bottom:4px;padding:5px;border-radius:5px;border:0">' +
      '<input id="k-pins" placeholder="Закрепы: ID/полный URL через запятую" style="box-sizing:border-box;width:100%;margin-bottom:4px;padding:5px;border-radius:5px;border:0">' +
      '<div style="display:flex;gap:4px;align-items:center;margin-bottom:6px">' +
      '<label style="flex:1;background:#333;padding:5px;border-radius:5px;cursor:pointer">Overview.csv (необязательно)<input id="k-overview" type="file" accept=".csv,text/csv" style="display:none"></label>' +
      '<input id="k-year" type="number" min="2020" max="2100" title="Год первой строки Overview" style="width:64px;padding:5px;border:0;border-radius:5px">' +
      '</div><div style="display:flex;gap:4px;flex-wrap:wrap">' +
      '<button id="k-new" style="flex:1 0 30%;cursor:pointer;border:0;border-radius:5px;padding:6px;background:#555;color:#fff">Новый сбор</button>' +
      '<button id="k-dry" style="flex:1 0 30%;cursor:pointer;border:0;border-radius:5px;padding:6px;background:#555;color:#fff">Проверить</button>' +
      '<button id="k-go" style="flex:1 0 30%;cursor:pointer;border:0;border-radius:5px;padding:6px;background:#2d8cff;color:#fff">Старт</button>' +
      '<button id="k-resume" style="flex:1 0 30%;cursor:pointer;border:0;border-radius:5px;padding:6px;background:#c60;color:#fff">Продолжить</button>' +
      '<button id="k-stop" style="flex:1 0 30%;cursor:pointer;border:0;border-radius:5px;padding:6px;background:#a33;color:#fff">Пауза</button>' +
      '<button id="k-up" style="flex:1 0 30%;cursor:pointer;border:0;border-radius:5px;padding:6px;background:#393;color:#fff">Залить</button>' +
      '<button id="k-dl" style="flex:1 0 30%;cursor:pointer;border:0;border-radius:5px;padding:6px;background:#555;color:#fff">Скачать</button>' +
      '</div><div id="k-st" style="margin-top:7px;color:#bbb;min-height:28px"></div>';
    document.body.appendChild(el);
    el.querySelector('#k-ep').value = GM_getValue(K.ep, '');
    el.querySelector('#k-tok').value = GM_getValue(K.tok, '');
    el.querySelector('#k-pins').value = getJSON(K.pins, []).join(', ');
    el.querySelector('#k-year').value = GM_getValue(K.year, new Date().getFullYear());
    el.querySelector('#k-ep').onchange = (event) => GM_setValue(K.ep, event.target.value.trim());
    el.querySelector('#k-tok').onchange = (event) => GM_setValue(K.tok, event.target.value.trim());
    el.querySelector('#k-year').onchange = (event) => GM_setValue(K.year, event.target.value);
    el.querySelector('#k-overview').onchange = (event) => readOverview(event.target.files && event.target.files[0]);
    el.querySelector('#k-new').onclick = newBatch;
    el.querySelector('#k-dry').onclick = () => {
      scanDomIds();
      const parsed = savePins();
      const catalog = catalogState();
      const count = allIds().size;
      status(count
        ? 'Найдено ' + count + ' публикаций (каталог ' + catalog.ids.size + ', страниц ' + catalog.pages + ', конец: ' + (catalog.complete ? 'да' : 'НЕТ') + ', закрепы ' + getJSON(K.pins, []).length + ').' + (catalog.complete ? ' Сверь число с TikTok перед стартом.' : ' Нельзя стартовать: нажми «Новый сбор» на странице «Публикации» и после перезагрузки промотай до конца.') + (parsed.rejected ? ' Не распознано значений: ' + parsed.rejected + '.' : '')
        : 'Каталог пуст. Вручную промотай «Публикации» до конца.');
      paint();
    };
    el.querySelector('#k-go').onclick = startWalk;
    el.querySelector('#k-resume').onclick = resumeWalk;
    el.querySelector('#k-stop').onclick = stopWalk;
    el.querySelector('#k-up').onclick = () => upload(false);
    el.querySelector('#k-dl').onclick = download;
    paint();
  }

  function boot() {
    mountUI();
    scanDomIds();
    setInterval(() => {
      if (GM_getValue(K.mode, 'idle') !== 'walking') {
        scanDomIds();
        paint();
      }
    }, 2000);
    const notice = GM_getValue(K.notice, '');
    if (notice) {
      GM_deleteValue(K.notice);
      status(notice);
    }
    if (!checkPageSafety()) return;
    if (GM_getValue(K.mode, 'idle') === 'walking') {
      if (!claimOwner()) {
        status('Эта вкладка не продолжает обход: активна другая вкладка.');
        paint();
        return;
      }
      status('Страница загружена. Жду 15 секунд перед следующим шагом…');
      setTimeout(completePageAndAdvance, PAGE_DELAY_MS);
    }
  }

  initState();
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', boot);
  else boot();
})();
