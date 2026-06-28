// ==UserScript==
// @name         Контур — TikTok Studio capture & auto-walk (B+)
// @namespace    kontur.rosta
// @version      2.2
// @description  Пассивно собирает богатую per-video аналитику из залогиненного TikTok Studio и (опц.) сам обходит видео человеческим темпом, заливая JSON на ingest коннектора kontur.tiktok. Перечисление — из ответов item_list (не из DOM), страницы ключуются по составу id (cursor у TikTok не уникален). Ничего не форжит — читает ответы, что страница и так грузит.
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
 БЕЗОПАСНОСТЬ: скрипт НЕ шлёт запросы в TikTok и НЕ передаёт сессию. Он ЧИТАЕТ
 JSON-ответы аналитики, которые страница и так загружает. Подпись (webmssdk/X-Bogus)
 делает сама страница. Авто-обход = переход по твоим же URL аналитики человеческим
 темпом — выглядит как ты сам листаешь Studio. Это твой основной аккаунт: темп
 щадящий, и перед обходом ОБЯЗАТЕЛЕН «Сухой прогон» (показывает, сколько видео нашёл).

 НАСТРОЙКА: вписать в плашке Endpoint (напр. https://thedialog.ru/ingest/tiktok)
 и Token (= TIKTOK_INGEST_TOKEN с сервера). Открыть «Публикации» и ОДИН РАЗ
 медленно промотать список до конца (DOM виртуализирован — перечисление берётся из
 ответов item_list, которые подгружаются при прокрутке) → «Сухой прогон» покажет
 число видео → «Старт обход». По концу обхода JSON сам уйдёт на сервер (или «Скачать»).
*/

(function () {
  'use strict';
  const W = typeof unsafeWindow !== 'undefined' ? unsafeWindow : window;

  // --- персистентное состояние (переживает переходы между страницами) ---
  const K = { cap: 'k_cap', queue: 'k_queue', mode: 'k_mode', ep: 'k_ep', tok: 'k_tok', hb: 'k_hb' };
  const getJSON = (k, d) => { try { return JSON.parse(GM_getValue(k, '')) ?? d; } catch (e) { return d; } };
  const setJSON = (k, v) => GM_setValue(k, JSON.stringify(v));

  const WANT = (u) =>
    /\/aweme\/v2\/data\/insight|comment\/v1\/get_key_words|ai_comment\/analytics_overview|creator\/manage\/item_list/.test(u);
  const isItemList = (u) => /creator\/manage\/item_list/.test(u);

  function awemeId(url) {
    const m = /type_requests=([^&]+)/.exec(url);
    if (m) { try { const a = JSON.parse(decodeURIComponent(m[1])); const o = a.find((x) => x && x.aweme_id); if (o) return o.aweme_id; } catch (e) {} }
    const v = /[?&](?:vid|item_id|aweme_id)=(\d+)/.exec(url);
    return v ? v[1] : '_';
  }

  function hash(s) {  // дешёвый стабильный хеш строки (djb2)
    let h = 5381;
    for (let i = 0; i < s.length; i++) h = ((h << 5) + h + s.charCodeAt(i)) | 0;
    return (h >>> 0).toString(36);
  }
  function record(url, json) {
    if (!WANT(url) || !json || typeof json !== 'object') return;
    const cap = getJSON(K.cap, {});
    // item_list грузится СТРАНИЦАМИ ПРИ ЗАГРУЗКЕ страницы; cursor у TikTok НЕ уникален
    // (видели [50,90,null,null,50,...]) → дедуп по cursor ЗАТИРАЛ страницы (выходило 62
    // из 105). Ключуем по хешу всех id страницы: разные страницы → разные ключи (копим),
    // повторный фетч той же страницы → тот же ключ (дедуп).
    let key;
    if (isItemList(url)) {
      const ids = (json.item_list || []).map((it) => it && it.item_id).filter(Boolean);
      key = 'itemlist|' + ids.length + '|' + hash(ids.join(','));
    } else {
      key = awemeId(url) + '|' + url.split('?')[0] + '|' + Object.keys(json).sort().join(',');
    }
    cap[key] = { url, json };
    setJSON(K.cap, cap);
    paint();
  }

  // Все aweme_id из захваченных страниц item_list (перечисление без DOM-скрапа).
  function catalogIds() {
    const ids = new Set();
    Object.values(getJSON(K.cap, {})).forEach((e) => {
      if (e && isItemList(e.url || '') && e.json && Array.isArray(e.json.item_list)) {
        e.json.item_list.forEach((it) => { if (it && it.item_id) ids.add(String(it.item_id)); });
      }
    });
    return ids;
  }

  // --- перехват ответов (только чтение) ---
  const _fetch = W.fetch;
  W.fetch = async function (...a) {
    const r = await _fetch.apply(this, a);
    try { const u = typeof a[0] === 'string' ? a[0] : (a[0] && a[0].url) || ''; if (WANT(u)) record(u, JSON.parse(await r.clone().text())); } catch (e) {}
    return r;
  };
  const _open = W.XMLHttpRequest.prototype.open, _send = W.XMLHttpRequest.prototype.send;
  W.XMLHttpRequest.prototype.open = function (m, u) { this.__u = u; return _open.apply(this, arguments); };
  W.XMLHttpRequest.prototype.send = function () {
    this.addEventListener('load', () => { try { if (WANT(this.__u)) record(this.__u, JSON.parse(this.responseText)); } catch (e) {} });
    return _send.apply(this, arguments);
  };

  // --- перечисление видео ---
  // Источник — захваченные страницы item_list (catalogIds). DOM «Публикаций»
  // виртуализирован (всегда ~8 строк, прокруткой из скрипта не вычерпать —
  // синтетические события react-window игнорирует), зато ответ item_list страница
  // за страницей содержит ВЕСЬ каталог. Достаточно один раз промотать «Публикации»
  // до конца — хук поймает все страницы; здесь читаем накопленное.
  function collectIds() {
    const ids = catalogIds();
    // запасной путь — если в DOM всё же есть прямые ссылки
    document.querySelectorAll('a[href*="/video/"],a[href*="/photo/"],a[href*="/analytics/"]').forEach((a) => {
      const m = /\/(?:video|photo|analytics)\/(\d{6,})/.exec(a.getAttribute('href') || '');
      if (m) ids.add(m[1]);
    });
    return [...ids];
  }

  const rnd = (a, b) => a + Math.floor(Math.random() * (b - a));
  const stepUrl = (s) => 'https://www.tiktok.com/tiktokstudio/analytics/' + s.id + (s.tab ? '/' + s.tab : '');

  // --- драйвер обхода (между переходами состояние в GM) ---
  async function advance() {
    const q = getJSON(K.queue, []);
    if (!q.length) { return finishWalk(); }
    const next = q.shift();
    setJSON(K.queue, q);
    paint();
    location.href = stepUrl(next);
  }
  function finishWalk() {
    GM_setValue(K.mode, 'idle');
    upload(true);
  }
  function startWalk() {
    const ids = collectIds();
    if (!ids.length) { status('Видео не найдены. Открой «Публикации» и промотай список до конца, потом «Сухой прогон».'); return; }
    const q = [];
    ids.forEach((id) => { q.push({ id, tab: '' }); q.push({ id, tab: 'viewers' }); q.push({ id, tab: 'engagement' }); });
    setJSON(K.queue, q);
    GM_setValue(K.mode, 'walking');
    paint();
    advance();
  }

  // --- заливка на ingest (cross-origin через GM_xmlhttpRequest) ---
  function upload(auto) {
    const ep = GM_getValue(K.ep, ''), tok = GM_getValue(K.tok, '');
    const cap = Object.values(getJSON(K.cap, {}));
    if (!ep || !tok) { if (!auto) status('Впиши Endpoint и Token в плашке.'); return; }
    if (!cap.length) { if (!auto) status('Нечего заливать — сначала собери данные.'); return; }
    GM_xmlhttpRequest({
      method: 'POST', url: ep,
      headers: { 'Content-Type': 'application/json', 'X-Kontur-Token': tok },
      data: JSON.stringify({ capture: cap }),
      onload: (r) => {
        if (r.status === 200) { GM_setValue(K.cap, '{}'); GM_setValue(K.hb, new Date().toISOString()); status('Залито ✓ ' + r.responseText.slice(0, 80)); }
        else status('Ошибка заливки ' + r.status + ': ' + r.responseText.slice(0, 120));
        paint();
      },
      onerror: () => status('Сеть/CORS: заливка не удалась'),
    });
  }
  function download() {
    const cap = Object.values(getJSON(K.cap, {}));
    const blob = new Blob([JSON.stringify(cap)], { type: 'application/json' });
    const a = document.createElement('a');
    a.href = URL.createObjectURL(blob); a.download = 'tiktok_capture_' + new Date().toISOString().slice(0, 10) + '.json'; a.click();
  }

  // --- UI ---
  let el;
  function status(s) { if (el) el.querySelector('#k-st').textContent = s; }
  function paint() {
    if (!el) return;
    const cap = getJSON(K.cap, {});
    const vids = catalogIds();  // из item_list
    Object.keys(cap).forEach((k) => { if (!k.startsWith('itemlist|')) vids.add(k.split('|')[0]); });  // + пройденные обходом
    const q = getJSON(K.queue, []), mode = GM_getValue(K.mode, 'idle'), hb = GM_getValue(K.hb, '—');
    el.querySelector('#k-n').textContent = `${vids.size} видео · ${Object.keys(cap).length} захватов` +
      (mode === 'walking' ? ` · обход: осталось ${q.length}` : '') + ` · залито: ${hb}`;
  }
  function mountUI() {
    el = document.createElement('div');
    el.style.cssText = 'position:fixed;right:14px;bottom:14px;z-index:2147483647;background:#111;color:#fff;font:12px/1.4 -apple-system,sans-serif;padding:10px;border-radius:10px;box-shadow:0 4px 16px rgba(0,0,0,.35);width:280px;opacity:.95';
    el.innerHTML =
      '<div style="font-weight:600;margin-bottom:6px">Контур · TikTok B+</div>' +
      '<div id="k-n" style="color:#7fd;margin-bottom:6px">—</div>' +
      '<input id="k-ep" placeholder="Endpoint /ingest/tiktok" style="width:100%;margin-bottom:4px;padding:4px;border-radius:5px;border:0">' +
      '<input id="k-tok" placeholder="Token" style="width:100%;margin-bottom:6px;padding:4px;border-radius:5px;border:0">' +
      '<div style="display:flex;gap:4px;flex-wrap:wrap">' +
      '<button id="k-dry" style="flex:1;cursor:pointer;border:0;border-radius:5px;padding:5px;background:#555;color:#fff">Сухой прогон</button>' +
      '<button id="k-go" style="flex:1;cursor:pointer;border:0;border-radius:5px;padding:5px;background:#2d8cff;color:#fff">Старт обход</button>' +
      '<button id="k-stop" style="flex:1;cursor:pointer;border:0;border-radius:5px;padding:5px;background:#a33;color:#fff">Стоп</button>' +
      '<button id="k-up" style="flex:1;cursor:pointer;border:0;border-radius:5px;padding:5px;background:#393;color:#fff">Залить</button>' +
      '<button id="k-dl" style="flex:1;cursor:pointer;border:0;border-radius:5px;padding:5px;background:#555;color:#fff">Скачать</button>' +
      '</div><div id="k-st" style="margin-top:6px;color:#bbb;min-height:14px"></div>';
    document.body.appendChild(el);
    el.querySelector('#k-ep').value = GM_getValue(K.ep, '');
    el.querySelector('#k-tok').value = GM_getValue(K.tok, '');
    el.querySelector('#k-ep').onchange = (e) => GM_setValue(K.ep, e.target.value.trim());
    el.querySelector('#k-tok').onchange = (e) => GM_setValue(K.tok, e.target.value.trim());
    el.querySelector('#k-dry').onclick = () => {
      const n = collectIds().length;
      status(n
        ? 'Найдено видео: ' + n + '. Меньше, чем в «Публикациях»? Промотай список ниже и нажми снова. Иначе «Старт обход».'
        : 'Открой «Публикации» и медленно промотай список до конца — потом сюда «Сухой прогон».');
      paint();
    };
    el.querySelector('#k-go').onclick = startWalk;
    el.querySelector('#k-stop').onclick = () => { GM_setValue(K.mode, 'idle'); setJSON(K.queue, []); status('Обход остановлен'); paint(); };
    el.querySelector('#k-up').onclick = () => upload(false);
    el.querySelector('#k-dl').onclick = download;
    paint();
  }

  // --- запуск: на каждой загрузке монтируем UI; если идёт обход — ждём захват и шагаем дальше ---
  function boot() {
    mountUI();
    if (GM_getValue(K.mode, 'idle') === 'walking') {
      setTimeout(advance, rnd(5000, 9000)); // дать вкладке прогрузить аналитику + человеческий темп
    }
  }
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', boot);
  else boot();
})();
