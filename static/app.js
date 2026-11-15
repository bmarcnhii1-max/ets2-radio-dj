/* ETS2 Radio DJ — app.js (Spotify-минимал, RU, SVG-иконки) */
'use strict';

let currentDuration = 180;
let currentSec = 0;
let isBrowserTesting = false;
let currentPickMode = "vibe";
let oauthPollTimer = null;
let oauthPollIntervalSec = 5;
let allLoadedPicks = [];
let currentPageIndex = 0;
let pendingTrack = null;
let lastProfilesSig = "";
let activeProfileName = "Мой профиль";
let lastStatusVideoId = "";
let currentTrackData = null;
let libraryMode = 'history';
let draggedQueueIndex = null;
let quickPicksRequestId = 0;
let statusRequestId = 0;
let playlistsRequestId = 0;
let toastTimer = null;

/* Подборки: 4 колонки x 3 ряда = 12 карточек на страницу */
const PICKS_PAGE_SIZE = 12;

/* Текст песни */
let lyricsShown = false;
let lyricsData = null;
let lyricsVideoId = null;

/* Визуализатор */
let audioCtx = null;
let analyserNode = null;
let vizCanvas = null;
let vizRunning = false;

/* Телеметрия */

/* SVG-иконка из спрайта (определён в index.html) */
function icon(name, cls = 'icon') {
  return `<svg class="${cls}"><use href="#i-${name}"/></svg>`;
}

function esc(s) {
  return String(s ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
}

function plural(n, one, few, many) {
  const mod10 = n % 10, mod100 = n % 100;
  if (mod10 === 1 && mod100 !== 11) return one;
  if (mod10 >= 2 && mod10 <= 4 && (mod100 < 12 || mod100 > 14)) return few;
  return many;
}

function showToast(msg, duration = 3000) {
  const t = document.getElementById('toast');
  t.textContent = msg;
  t.classList.add('show');
  if (toastTimer) clearTimeout(toastTimer);
  toastTimer = setTimeout(() => t.classList.remove('show'), duration);
}

function switchTab(id) {
  document.querySelectorAll('.tab-btn').forEach(b => b.classList.toggle('active', b.dataset.tab === id));
  document.querySelectorAll('.tab-content').forEach(c => c.classList.remove('active'));
  const panel = document.getElementById(id);
  if (panel) panel.classList.add('active');
}

function switchAuthSub(type) {
  const btnC = document.getElementById('subBtnCookie');
  const btnO = document.getElementById('subBtnOAuth');
  const subC = document.getElementById('authSubCookie');
  const subO = document.getElementById('authSubOAuth');
  if (type === 'cookie') {
    btnC.classList.add('active'); btnO.classList.remove('active');
    subC.style.display = 'block'; subO.style.display = 'none';
  } else {
    btnO.classList.add('active'); btnC.classList.remove('active');
    subO.style.display = 'block'; subC.style.display = 'none';
  }
}

function formatTime(s) {
  s = Math.max(0, parseInt(s) || 0);
  const m = Math.floor(s / 60);
  const sec = s % 60;
  return `${m.toString().padStart(2, '0')}:${sec.toString().padStart(2, '0')}`;
}

/* ============ БЫСТРЫЙ ВЫБОР ============ */

function setPickMode(mode, btnEl) {
  currentPickMode = mode;
  document.querySelectorAll('.mode-chip').forEach(c => c.classList.remove('active'));
  if (btnEl) btnEl.classList.add('active');
  currentPageIndex = 0;
  loadQuickPicks(true);
}

function switchPage(idx) {
  const pages = Math.min(3, Math.max(1, Math.ceil(allLoadedPicks.length / PICKS_PAGE_SIZE)));
  currentPageIndex = Math.max(0, Math.min(idx, pages - 1));
  document.querySelectorAll('.carousel-dot').forEach((d, i) => {
    d.classList.toggle('active', i === currentPageIndex);
  });
  renderPicksPage();
}

function updateDots() {
  const box = document.getElementById('carouselDots');
  if (!box) return;
  const pages = Math.min(3, Math.max(1, Math.ceil(allLoadedPicks.length / PICKS_PAGE_SIZE)));
  if (currentPageIndex >= pages) currentPageIndex = pages - 1;
  box.innerHTML = '';
  for (let i = 0; i < pages; i++) {
    const d = document.createElement('div');
    d.className = 'carousel-dot' + (i === currentPageIndex ? ' active' : '');
    d.onclick = () => switchPage(i);
    box.appendChild(d);
  }
  box.style.display = allLoadedPicks.length === 0 ? 'none' : '';
}

function renderPicksPage() {
  const grid = document.getElementById('quickPicksGrid');
  grid.innerHTML = '';
  const start = currentPageIndex * PICKS_PAGE_SIZE;
  const pageItems = allLoadedPicks.slice(start, start + PICKS_PAGE_SIZE);

  if (pageItems.length === 0) {
    grid.innerHTML = `<div style="grid-column: 1 / -1; text-align: center; color: var(--text-muted); padding: 24px;">На этой странице треков нет.</div>`;
    updateDots();
    return;
  }

  pageItems.forEach(p => {
    const card = document.createElement('div');
    card.className = 'pick-card';
    if (p.playlistId) {
      card.onclick = () => sendPlaylist(p.playlistId, p.title, p.artist, p.thumbnail);
    } else {
      card.onclick = () => sendQuickPick(p.videoId, p.title, p.artist, p.thumbnail);
    }

    const img = document.createElement('img');
    if (p.thumbnail) {
      img.src = p.thumbnail;
      img.alt = p.title || 'Трек';
      img.loading = 'lazy';
      card.appendChild(img);
    } else {
      const ph = document.createElement('div');
      ph.style.cssText = 'height:100%; display:flex; align-items:center; justify-content:center; font-size:24px; color:var(--text-muted);';
      ph.innerHTML = icon('music');
      card.appendChild(ph);
    }

    const rest = document.createElement('div');
    rest.innerHTML = `
      <div class="pick-play-icon">${icon('play')}</div>
      <div class="pick-overlay">
        <div class="pick-title" title="${esc(p.title)}">${esc(p.title)}</div>
        <div class="pick-artist" title="${esc(p.artist || '')}">${esc(p.artist || 'YouTube Music')}</div>
      </div>
    `;
    card.appendChild(rest);
    if (p.playlistId) {
      const tag = document.createElement('div');
      tag.className = 'pick-album-tag';
      tag.textContent = 'Альбом целиком';
      card.appendChild(tag);
    } else {
      const albBtn = document.createElement('button');
      albBtn.className = 'pick-album-btn';
      albBtn.title = 'Играть альбомом с первого трека';
      albBtn.innerHTML = icon('disc');
      albBtn.onclick = (e) => { e.stopPropagation(); sendAlbum(p.title, p.artist); };
      card.appendChild(albBtn);
    }
    grid.appendChild(card);
  });
  updateDots();
}

async function loadQuickPicks(force = false) {
  const grid = document.getElementById('quickPicksGrid');
  const requestId = ++quickPicksRequestId;
  const mode = currentPickMode;
  try {
    const res = await fetch(`/api/quick_picks?mode=${mode}${force ? '&force=1' : ''}`);
    const data = await res.json();
    if (requestId !== quickPicksRequestId || mode !== currentPickMode) return;
    if (!res.ok) throw new Error(data.error || ('HTTP ' + res.status));

    allLoadedPicks = data.picks || [];
    if (currentPageIndex > 0 && allLoadedPicks.length <= currentPageIndex * PICKS_PAGE_SIZE) currentPageIndex = 0;

    if (data.user_name) {
      document.getElementById('ytmUserName').textContent = data.user_name.toUpperCase();
      document.getElementById('ytmAvatarInitial').textContent = data.user_name.charAt(0).toUpperCase();
    }
    if (data.fallback && data.message) showToast(data.message);

    if (allLoadedPicks.length === 0) {
      grid.innerHTML = `<div style="grid-column: 1 / -1; text-align: center; color: var(--text-muted); padding: 24px;">${esc(data.message || 'Нет треков для отображения.')}</div>`;
      updateDots();
      return;
    }
    renderPicksPage();
  } catch (e) {
    if (requestId !== quickPicksRequestId || mode !== currentPickMode) return;
    grid.innerHTML = `<div style="grid-column: 1 / -1; text-align: center; color: var(--text-muted); padding: 20px;">Ошибка загрузки рекомендаций: ${esc(e.message || e)}</div>`;
  }
}

/* ============ ЗАПУСК ТРЕКОВ ============ */

async function sendQuickPick(videoId, title, artist, thumbnail) {
  showToast(`Запуск: ${title}`);
  pendingTrack = { videoId, title, artist, thumbnail, previousVideoId: lastStatusVideoId, until: Date.now() + 8000 };
  document.getElementById('trackTitle').textContent = title;
  document.getElementById('trackArtist').textContent = artist || "Запуск эфира...";
  if (thumbnail) document.getElementById('trackThumb').src = thumbnail;
  document.getElementById('sourceBadge').textContent = `● ПЕРЕКЛЮЧЕНИЕ...`;

  try {
    const res = await fetch('/api/change', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        query: videoId,
        offset: 0,
        title: title || '',
        artist: artist || '',
        thumbnail: thumbnail || ''
      })
    });
    const data = await res.json();
    if (!res.ok || !data.ok) throw new Error(data.error || 'Не удалось запустить трек');
    updateStatus();
    if (currentPickMode === 'radio') setTimeout(() => loadQuickPicks(true), 2500);
  } catch (e) {
    pendingTrack = null;
    showToast(e.message || 'Ошибка запуска трека');
    updateStatus();
  }
}

async function sendPlaylist(playlistId, title, artist, thumbnail) {
  showToast(`Запуск альбома: ${title}`);
  pendingTrack = { videoId: null, title: title, artist: artist || "Альбом", thumbnail: thumbnail || null, previousVideoId: lastStatusVideoId, until: Date.now() + 10000 };
  document.getElementById('trackTitle').textContent = title;
  document.getElementById('trackArtist').textContent = artist || "Альбом";
  document.getElementById('sourceBadge').textContent = `● ЗАГРУЗКА...`;
  try {
    const res = await fetch('/api/change', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ query: playlistId, offset: 0 })
    });
    const data = await res.json();
    if (!res.ok || !data.ok) throw new Error(data.error || 'Не удалось запустить плейлист');
    updateStatus();
  } catch (e) {
    pendingTrack = null;
    showToast(e.message || 'Ошибка запуска плейлиста');
    updateStatus();
  }
}

async function sendAlbum(title, artist) {
  const query = 'альбом ' + [title, artist].filter(Boolean).join(' ');
  showToast(`Запуск альбома: ${title}`);
  pendingTrack = { videoId: null, title: query, artist: "Загрузка треков альбома...", thumbnail: null, previousVideoId: lastStatusVideoId, until: Date.now() + 10000 };
  document.getElementById('trackTitle').textContent = query;
  document.getElementById('trackArtist').textContent = "Загрузка треков альбома...";
  document.getElementById('sourceBadge').textContent = `● ЗАГРУЗКА...`;
  try {
    const res = await fetch('/api/change', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ query, offset: 0 })
    });
    const data = await res.json();
    if (!res.ok || !data.ok) throw new Error(data.error || 'Не удалось запустить альбом');
    updateStatus();
  } catch (e) {
    pendingTrack = null;
    showToast(e.message || 'Ошибка запуска альбома');
    updateStatus();
  }
}

async function sendTrack(isAlbumMode = false) {
  let query = document.getElementById('trackInput').value.trim();
  const rawOffset = document.getElementById('offsetInput').value.trim();
  let offset = 0;
  if (rawOffset) {
    const parts = rawOffset.split(':').map(x => parseInt(x, 10));
    if (parts.length > 3 || parts.some(x => isNaN(x) || x < 0)) {
      showToast('Неверный формат старта (нужны секунды или Ч:М:С), начинаю с 0');
      offset = 0;
    } else {
      offset = parts.reduce((a, b) => a * 60 + b, 0);
    }
  }
  if (!query) return;

  const looksLikeUrl = /https?:\/\/|youtu\.be|youtube\.com|MPREb_|OLAK5uy_|list=|[?&]v=/.test(query);
  if (isAlbumMode && !looksLikeUrl && !/^(альбом|album)/i.test(query)) {
    query = 'альбом ' + query;
  }

  showToast(isAlbumMode ? "Запуск альбома..." : "Отправляем в эфир...");
  pendingTrack = { videoId: null, title: query, artist: isAlbumMode ? "Загрузка треков альбома..." : "Запуск эфира...", thumbnail: null, previousVideoId: lastStatusVideoId, until: Date.now() + 8000 };
  document.getElementById('trackTitle').textContent = query;
  document.getElementById('trackArtist').textContent = isAlbumMode ? "Загрузка треков альбома..." : "Запуск эфира...";
  document.getElementById('sourceBadge').textContent = `● ЗАГРУЗКА...`;

  try {
    const res = await fetch('/api/change', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ query, offset })
    });
    const data = await res.json();
    if (!res.ok || !data.ok) throw new Error(data.error || 'Не удалось запустить запрос');
    document.getElementById('trackInput').value = '';
    document.getElementById('offsetInput').value = '';
  } catch (e) {
    pendingTrack = null;
    document.getElementById('sourceBadge').textContent = '● ОШИБКА';
    throw e;
  }
  updateStatus();
}

async function searchTracks() {
  const query = document.getElementById('trackInput').value.trim();
  if (!query) return;
  const box = document.getElementById('searchResults');
  box.innerHTML = '<div class="muted" style="font-size:11px;">Поиск...</div>';
  try {
    const res = await fetch(`/api/search?q=${encodeURIComponent(query)}`);
    const data = await res.json();
    if (!res.ok || !data.ok) throw new Error(data.error || 'Ошибка поиска');
    renderSearchResults(data.results || []);
  } catch (e) {
    box.innerHTML = `<div class="error-text">${esc(e.message || e)}</div>`;
  }
}

function renderSearchResults(results) {
  const box = document.getElementById('searchResults');
  box.innerHTML = '';
  if (!results.length) {
    box.innerHTML = '<div class="muted" style="font-size:11px;">Ничего не найдено.</div>';
    return;
  }
  results.forEach(item => {
    const row = document.createElement('div');
    row.className = 'row-item';
    row.innerHTML = `<div class="search-result-main"><div class="search-result-title">${esc(item.title || 'Трек')}</div><div class="search-result-artist">${esc(item.artist || 'YouTube Music')}</div></div>`;
    const play = document.createElement('button');
    play.className = 'btn';
    play.style.padding = '4px 8px';
    play.innerHTML = icon('play');
    play.onclick = () => sendQuickPick(item.videoId, item.title, item.artist, item.thumbnail);
    row.appendChild(play);
    box.appendChild(row);
  });
}

/* ============ ОЧЕРЕДЬ ============ */

function renderQueue(queue, currentAlbum) {
  const card = document.getElementById('queueCard');
  const list = document.getElementById('queueItemsList');
  const albBadge = document.getElementById('albumBadge');
  const count = document.getElementById('queueCount');
  if (!card || !list) return;

  // Во время drag-reorder не перерисовываем: 1-секундный updateStatus
  // иначе стирает DOM и обрывает перетаскивание дольше 1 секунды.
  if (draggedQueueIndex !== null) return;

  if (currentAlbum) {
    albBadge.style.display = '';
    albBadge.textContent = `Альбом: ${currentAlbum}`;
  } else {
    albBadge.style.display = 'none';
  }

  if (!queue || queue.length === 0) {
    if (currentAlbum) {
      card.style.display = 'block';
      const note = document.getElementById('queueEmptyNote');
      if (note) note.style.display = 'none';
      list.innerHTML = `<div class="muted" style="font-size:12px; text-align:center; padding:8px;">Загружаю следующие рекомендации...</div>`;
      count.textContent = '';
    } else {
      card.style.display = 'none';
      const note = document.getElementById('queueEmptyNote');
      if (note) note.style.display = 'block';
    }
    return;
  }

  const noteEl = document.getElementById('queueEmptyNote');
  if (noteEl) noteEl.style.display = 'none';

  card.style.display = 'block';
  count.textContent = `${queue.length} в очереди`;
  list.innerHTML = '';

  queue.forEach((item, idx) => {
    const row = document.createElement('div');
    row.className = 'row-item draggable';
    row.dataset.videoId = item.videoId || '';
    row.draggable = true;
    row.onclick = () => { if (!row._dragging) sendQuickPick(item.videoId, item.title, item.artist, item.thumbnail); };
    row.ondragstart = (e) => {
      draggedQueueIndex = idx;
      row._dragging = true;
      row.classList.add('queue-row-active');
      e.dataTransfer.effectAllowed = 'move';
      try { e.dataTransfer.setData('text/plain', String(idx)); } catch (err) {}
    };
    row.ondragend = () => {
      draggedQueueIndex = null;
      row.classList.remove('queue-row-active');
      setTimeout(() => { row._dragging = false; }, 80);
    };
    row.ondragover = (e) => {
      e.preventDefault();
      row.classList.add('queue-row-active');
    };
    row.ondragleave = () => row.classList.remove('queue-row-active');
    row.ondrop = (e) => {
      e.preventDefault();
      row.classList.remove('queue-row-active');
      if (draggedQueueIndex === null || draggedQueueIndex === idx) return;
      reorderQueueIndexes(draggedQueueIndex, idx);
    };

    const isAlbTrack = item.album && !item.album.includes("Рекомендации");
    const tag = isAlbTrack ? `<span class="row-tag album">Альбом</span>` : `<span class="row-tag rec">Реком.</span>`;

    row.innerHTML = `
      <span class="row-drag" title="Перетащи для изменения порядка">${icon('grip')}</span>
      <div class="row-main">
        <span class="row-num">${idx + 1}</span>
        ${tag}
        <span class="row-title">${esc(item.title)}</span>
        <span class="row-artist">— ${esc(item.artist || 'YT Music')}</span>
      </div>
      <div class="row-actions">
        <button class="btn queue-up-btn" title="Выше">${icon('chevron-up')}</button>
        <button class="btn queue-down-btn" title="Ниже">${icon('chevron-down')}</button>
        <button class="btn queue-play-btn" title="Слушать">${icon('play')}</button>
        <button class="btn queue-remove-btn" title="Убрать из очереди" style="color:var(--red);">${icon('x')}</button>
      </div>
    `;
    row.querySelector('.queue-up-btn').onclick = (e) => { e.stopPropagation(); moveQueueItem(idx, -1); };
    row.querySelector('.queue-down-btn').onclick = (e) => { e.stopPropagation(); moveQueueItem(idx, 1); };
    row.querySelector('.queue-play-btn').onclick = (e) => {
      e.stopPropagation();
      sendQuickPick(item.videoId, item.title, item.artist, item.thumbnail);
    };
    row.querySelector('.queue-remove-btn').onclick = (e) => {
      e.stopPropagation();
      removeQueueTrack(item.videoId, item.title);
    };
    list.appendChild(row);
  });
}

// Drag-reorder по индексам: videoId может повторяться в очереди.
async function reorderQueueIndexes(fromIndex, toIndex) {
  try {
    const res = await fetch('/api/queue/reorder', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ from_index: fromIndex, to_index: toIndex })
    });
    const data = await res.json();
    if (!res.ok || !data.ok) throw new Error(data.error || 'Не удалось изменить очередь');
    updateStatus();
  } catch (e) {
    showToast(e.message || 'Ошибка очереди');
  }
}

async function reorderQueue(videoIds) {
  try {
    const res = await fetch('/api/queue/reorder', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ video_ids: videoIds })
    });
    const data = await res.json();
    if (!res.ok || !data.ok) throw new Error(data.error || 'Не удалось изменить очередь');
    updateStatus();
  } catch (e) {
    showToast(e.message || 'Ошибка очереди');
  }
}

function visibleQueueIds() {
  return Array.from(document.querySelectorAll('#queueItemsList [data-video-id]')).map(row => row.dataset.videoId).filter(Boolean);
}

// По индексам: videoId может повторяться в очереди (indexOf двигает первую копию)
function moveQueueItem(index, delta) {
  const ids = visibleQueueIds();
  const nextIndex = index + delta;
  if (index < 0 || index >= ids.length || nextIndex < 0 || nextIndex >= ids.length) return;
  [ids[index], ids[nextIndex]] = [ids[nextIndex], ids[index]];
  reorderQueue(ids);
}

async function removeQueueTrack(videoId, title = '') {
  try {
    const res = await fetch('/api/queue/remove', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ video_id: videoId })
    });
    const data = await res.json();
    if (!res.ok || !data.ok) throw new Error(data.error || 'Не удалось убрать трек');
    showToast(`Убрано: ${title || 'трек'}`);
    await updateStatus();
  } catch (e) {
    showToast(e.message || 'Ошибка удаления трека');
  }
}

async function dislikeCurrentTrack() {
  try {
    const res = await fetch('/api/queue/remove', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ current: true })
    });
    const data = await res.json();
    if (!res.ok || !data.ok) throw new Error(data.error || 'Не удалось заменить трек');
    showToast('Трек убран, подбираю замену');
    await updateStatus();
  } catch (e) {
    showToast(e.message || 'Ошибка замены трека');
  }
}

/* ============ УПРАВЛЕНИЕ ============ */

async function togglePause() {
  await fetch('/api/control', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ action: 'pause' })
  });
  updateStatus();
}

async function previousTrack() {
  showToast("Предыдущий трек...");
  document.getElementById('sourceBadge').textContent = "● ПЕРЕКЛЮЧЕНИЕ...";
  try {
    await fetch('/api/control', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ action: 'previous' })
    });
    updateStatus();
  } catch (e) {
    showToast("Не удалось вернуться к предыдущему треку");
  }
}

async function skipTrack() {
  showToast("Пропускаю трек...");
  document.getElementById('sourceBadge').textContent = "● ПЕРЕКЛЮЧЕНИЕ...";
  await fetch('/api/control', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ action: 'skip' })
  });
  updateStatus();
}

async function seekRelative(delta) {
  const newTime = Math.max(0, currentSec + delta);
  currentSec = newTime;
  document.getElementById('currentTime').textContent = formatTime(currentSec);
  const percent = Math.min(100, (currentSec / currentDuration) * 100);
  document.getElementById('timelineFill').style.width = percent + '%';

  await fetch('/api/control', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ action: 'seek', time: newTime })
  });
  updateStatus();
}

async function seekByClick(e) {
  const bar = document.getElementById('timelineBar');
  const rect = bar.getBoundingClientRect();
  if (!rect.width) return;
  const clickX = e.clientX - rect.left;
  const ratio = Math.max(0, Math.min(1, clickX / rect.width));
  const newTime = Math.floor(ratio * currentDuration);
  currentSec = newTime;
  document.getElementById('currentTime').textContent = formatTime(currentSec);
  document.getElementById('timelineFill').style.width = (ratio * 100) + '%';

  await fetch('/api/control', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ action: 'seek', time: newTime })
  });
  updateStatus();
}

/* ============ ТЕКСТ ПЕСНИ (LRCLIB) ============ */

function toggleLyrics() {
  lyricsShown = !lyricsShown;
  document.getElementById('lyricsPanel').classList.toggle('open', lyricsShown);
  if (lyricsShown) loadLyrics(true);
}

async function loadLyrics(force = false) {
  const box = document.getElementById('lyricsBox');
  const track = currentTrackData;
  if (!lyricsShown) return;
  if (!track || !track.video_id || (track.video_id || '').startsWith('station:')) {
    box.innerHTML = '<div class="lyrics-empty">Для радиостанции текста нет</div>';
    return;
  }
  if (lyricsVideoId === track.video_id && lyricsData && !force) {
    syncLyricsHighlight();
    return;
  }
  box.innerHTML = '<div class="lyrics-empty">Ищу текст…</div>';
  try {
    const params = new URLSearchParams({ video_id: track.video_id });
    const res = await fetch(`/api/lyrics?${params}`);
    const data = await res.json();
    if ((track.video_id) !== (currentTrackData && currentTrackData.video_id)) return; // трек сменился
    if (data.ok) {
      lyricsData = data;
      lyricsVideoId = track.video_id;
      if (data.synced && data.lrc) {
        renderSyncedLyrics(data.lrc);
      } else if (data.plain) {
        box.innerHTML = data.plain.split('\n').map(l => `<div class="lyrics-line">${esc(l) || '&nbsp;'}</div>`).join('');
      } else {
        box.innerHTML = '<div class="lyrics-empty">Текст не найден</div>';
      }
    } else {
      lyricsData = null;
      lyricsVideoId = track.video_id;
      box.innerHTML = '<div class="lyrics-empty">Текст не найден</div>';
    }
  } catch (e) {
    box.innerHTML = `<div class="lyrics-empty error-text">${esc(e.message || e)}</div>`;
  }
}

function renderSyncedLyrics(lrc) {
  const box = document.getElementById('lyricsBox');
  box.innerHTML = '';
  const lines = lrc.split('\n');
  lines.forEach(line => {
    const m = line.match(/^\[(\d+):(\d+(?:\.\d+)?)\](.*)$/);
    if (!m) return;
    const t = parseInt(m[1], 10) * 60 + parseFloat(m[2]);
    const text = m[3].trim();
    const div = document.createElement('div');
    div.className = 'lyrics-line';
    div.dataset.time = t;
    div.textContent = text;
    div.onclick = () => seekTo(t);
    box.appendChild(div);
  });
  syncLyricsHighlight();
}

function syncLyricsHighlight() {
  if (!lyricsData || !lyricsData.synced) return;
  const box = document.getElementById('lyricsBox');
  const lines = box.querySelectorAll('.lyrics-line');
  let active = null;
  lines.forEach(l => {
    const t = parseFloat(l.dataset.time);
    if (t <= currentSec + 0.3) active = l;
    l.classList.remove('current');
  });
  if (active) {
    active.classList.add('current');
    const boxRect = box.getBoundingClientRect();
    const rect = active.getBoundingClientRect();
    if (rect.bottom > boxRect.bottom + 4 || rect.top < boxRect.top - 4) {
      box.scrollTop = active.offsetTop - box.clientHeight / 3;
    }
  }
}

function seekTo(t) {
  fetch('/api/control', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ action: 'seek', time: Math.floor(t) })
  }).then(() => updateStatus()).catch(() => {});
}

/* ============ ВИЗУАЛИЗАТОР ============ */

function setupVisualizer() {
  const audio = document.getElementById('browserTester');
  vizCanvas = document.getElementById('visualizer');
  if (!audio || !vizCanvas) return;
  if (!audioCtx) {
    try {
      audioCtx = new (window.AudioContext || window.webkitAudioContext)();
      const src = audioCtx.createMediaElementSource(audio);
      analyserNode = audioCtx.createAnalyser();
      analyserNode.fftSize = 128;
      src.connect(analyserNode);
      analyserNode.connect(audioCtx.destination);
    } catch (e) {
      console.warn('Visualizer setup failed:', e);
      return;
    }
  }
  drawVisualizer();
}

function drawVisualizer() {
  if (!analyserNode || !vizCanvas) return;
  const ctx = vizCanvas.getContext('2d');
  const data = new Uint8Array(analyserNode.frequencyBinCount);
  const W = vizCanvas.width = vizCanvas.clientWidth;
  const H = vizCanvas.height = vizCanvas.clientHeight;

  function frame() {
    if (!isBrowserTesting) {
      vizCanvas.style.display = 'none';
      vizRunning = false;
      return;
    }
    vizCanvas.style.display = 'block';
    requestAnimationFrame(frame);
    analyserNode.getByteFrequencyData(data);
    ctx.clearRect(0, 0, W, H);
    const bars = 48;
    const step = Math.floor(data.length / bars);
    const barW = W / bars;
    for (let i = 0; i < bars; i++) {
      let v = 0;
      for (let j = 0; j < step; j++) v += data[i * step + j];
      v = v / step / 255;
      const h = Math.max(2, v * (H - 6));
      ctx.fillStyle = '#1db954';
      ctx.globalAlpha = 0.5 + v * 0.5;
      ctx.fillRect(i * barW + 1, H - h, barW - 2, h);
    }
    ctx.globalAlpha = 1;
  }
  if (!vizRunning) {
    vizRunning = true;
    requestAnimationFrame(frame);
  }
}

function toggleBrowserTest() {
  const audio = document.getElementById('browserTester');
  const btn = document.getElementById('testAudioBtn');

  if (!isBrowserTesting) {
    audio.src = window.location.origin + "/radio.mp3?" + Date.now();
    audio.play().then(() => setupVisualizer()).catch(() => {
      showToast('Браузер не разрешил запустить тест звука');
      audio.src = "";
      btn.textContent = "Тест звука";
      isBrowserTesting = false;
    });
    btn.textContent = "Остановить тест";
    isBrowserTesting = true;
    showToast("Тест звука запущен");
  } else {
    audio.pause();
    audio.src = "";
    btn.textContent = "Тест звука";
    isBrowserTesting = false;
    if (vizCanvas) vizCanvas.style.display = 'none';
  }
}

/* ============ БИБЛИОТЕКА ============ */

async function loadLibrary(mode = libraryMode) {
  libraryMode = mode;
  document.getElementById('historyTabBtn').classList.toggle('active', mode === 'history');
  document.getElementById('favoritesTabBtn').classList.toggle('active', mode === 'favorites');
  const box = document.getElementById('libraryList');
  box.innerHTML = '<div class="muted" style="font-size:11px;">Загрузка...</div>';
  try {
    const endpoint = mode === 'favorites' ? '/api/favorites' : '/api/history?limit=30';
    const res = await fetch(endpoint);
    const data = await res.json();
    if (!res.ok || !data.ok) throw new Error(data.error || 'Не удалось загрузить библиотеку');
    const items = mode === 'favorites' ? (data.favorites || []) : (data.history || []);
    box.innerHTML = '';
    if (!items.length) {
      box.innerHTML = '<div class="muted" style="font-size:11px;">Пока пусто.</div>';
      return;
    }
    items.forEach(item => {
      const row = document.createElement('div');
      row.className = 'row-item';
      row.innerHTML = `<div class="library-row-main"><div class="library-row-title">${esc(item.title || 'Трек')}</div><div class="library-row-artist">${esc(item.artist || 'YouTube Music')}</div></div>`;
      const play = document.createElement('button');
      play.className = 'btn';
      play.style.padding = '4px 8px';
      play.innerHTML = icon('play');
      play.onclick = () => sendQuickPick(item.videoId, item.title, item.artist, item.thumbnail);
      row.appendChild(play);
      if (mode === 'favorites') {
        const remove = document.createElement('button');
        remove.className = 'btn';
        remove.style.padding = '4px 7px';
        remove.style.color = 'var(--red)';
        remove.innerHTML = icon('x');
        remove.onclick = async () => {
          try {
            const res = await fetch('/api/favorites/toggle', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ video_id: item.videoId }) });
            const data = await res.json().catch(() => ({}));
            if (!res.ok || data.ok === false) throw new Error(data.error || 'Не удалось удалить из избранного');
          } finally {
            loadLibrary('favorites');
            updateStatus();
          }
        };
        row.appendChild(remove);
      }
      box.appendChild(row);
    });
  } catch (e) {
    box.innerHTML = `<div class="error-text">${esc(e.message || e)}</div>`;
  }
}

async function toggleFavoriteCurrent() {
  if (!currentTrackData || !currentTrackData.video_id || (currentTrackData.video_id || '').startsWith('station:')) return;
  try {
    const res = await fetch('/api/like', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        video_id: currentTrackData.video_id,
        title: currentTrackData.title,
        artist: currentTrackData.artist,
        thumbnail: currentTrackData.thumbnail,
        album: currentTrackData.album
      })
    });
    const data = await res.json();
    if (!res.ok || !data.ok) throw new Error(data.error || 'Не удалось изменить избранное');
    const liked = data.liked === undefined ? !!data.favorited : !!data.liked;
    if (liked) {
      showToast(data.account ? 'Лайк поставлен в аккаунте' : ('Только локально: ' + (data.message || 'аккаунт недоступен')));
    } else {
      showToast('Удалено из избранного');
    }
    updateStatus();
    if (libraryMode === 'favorites') loadLibrary('favorites');
  } catch (e) {
    showToast(e.message || 'Ошибка избранного');
  }
}

/* ============ ПЛЕЙЛИСТЫ YT ============ */

async function playPlaylist(playlistId, title) {
  showToast(`Запуск плейлиста: ${title}`);
  pendingTrack = { videoId: null, title: "Загрузка плейлиста...", artist: title, thumbnail: null, previousVideoId: lastStatusVideoId, until: Date.now() + 10000 };
  document.getElementById('trackTitle').textContent = "Загрузка плейлиста...";
  document.getElementById('trackArtist').textContent = title;
  document.getElementById('sourceBadge').textContent = "● ЗАГРУЗКА ПЛЕЙЛИСТА...";
  try {
    const res = await fetch('/api/change', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ query: playlistId, offset: 0 })
    });
    const data = await res.json();
    if (!res.ok || !data.ok) throw new Error(data.error || 'Ошибка запуска плейлиста');
    updateStatus();
  } catch (e) {
    showToast(e.message || "Ошибка запуска плейлиста");
  }
}

async function loadUserPlaylists(force = false) {
  const grid = document.getElementById('userPlaylistsGrid');
  if (!grid) return;
  const requestId = ++playlistsRequestId;
  try {
    const res = await fetch('/api/user_playlists');
    const data = await res.json();
    if (requestId !== playlistsRequestId) return;
    if (!res.ok || !data.ok) throw new Error(data.error || 'Плейлисты недоступны');
    grid.innerHTML = '';
    if (!(data.playlists || []).length) {
      grid.innerHTML = '<span class="muted" style="font-size:12px;">Войди в аккаунт, чтобы увидеть плейлисты</span>';
      return;
    }
    for (const p of (data.playlists || [])) {
      const chip = document.createElement('button');
      chip.className = 'playlist-chip';
      chip.innerHTML = (p.id === 'LM' ? icon('heart', 'icon icon-stroke') + ' ' : icon('play', 'icon') + ' ') + esc(p.title || 'Плейлист');
      if (p.count) {
        const count = document.createElement('span');
        count.className = 'count';
        count.textContent = `(${p.count})`;
        chip.appendChild(count);
      }
      chip.onclick = () => playPlaylist(p.id, p.title || 'Плейлист');
      grid.appendChild(chip);
    }
  } catch (e) {
    if (requestId === playlistsRequestId) {
      grid.innerHTML = '<span class="muted" style="font-size:12px;">Плейлисты недоступны</span>';
    }
  }
}

/* ============ СОБСТВЕННЫЕ ПЛЕЙЛИСТЫ ============ */

async function createPlaylist() {
  const name = prompt('Название нового плейлиста:');
  if (!name || !name.trim()) return;
  try {
    const res = await fetch('/api/playlists/create', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ name: name.trim() })
    });
    const data = await res.json();
    if (!res.ok || !data.ok) throw new Error(data.error || 'Не удалось создать');
    showToast(`Плейлист "${data.playlist.name}" создан`);
    loadOwnPlaylists();
  } catch (e) {
    showToast(e.message || 'Ошибка создания плейлиста');
  }
}

async function addToOwnPlaylist(playlistId, playlistName) {
  try {
    const res = await fetch('/api/playlists/add', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ playlist_id: playlistId })
    });
    const data = await res.json();
    if (!res.ok || !data.ok) throw new Error(data.error || 'Не удалось добавить');
    showToast(`Добавлено в "${playlistName}"`);
    loadOwnPlaylists();
  } catch (e) {
    showToast(e.message || 'Ошибка добавления трека');
  }
}

async function removeFromOwnPlaylist(playlistId, videoId) {
  try {
    const res = await fetch('/api/playlists/remove_track', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ playlist_id: playlistId, video_id: videoId })
    });
    const data = await res.json();
    if (!res.ok || !data.ok) throw new Error(data.error || 'Не удалось удалить');
    loadOwnPlaylists();
  } catch (e) {
    showToast(e.message || 'Ошибка удаления');
  }
}

async function deleteOwnPlaylist(playlistId, name) {
  if (!confirm(`Удалить плейлист "${name}"?`)) return;
  try {
    await fetch('/api/playlists/delete', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ playlist_id: playlistId })
    });
    showToast('Плейлист удалён');
    loadOwnPlaylists();
  } catch (e) {
    showToast(e.message || 'Ошибка удаления');
  }
}

async function playOwnPlaylist(playlistId, name) {
  try {
    const res = await fetch('/api/playlists/play', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ playlist_id: playlistId })
    });
    const data = await res.json();
    if (!res.ok || !data.ok) throw new Error(data.error || 'Не удалось запустить');
    showToast(`${name} (${data.count} ${plural(data.count, 'трек', 'трека', 'треков')})`);
    pendingTrack = { videoId: null, title: name, artist: 'Свой плейлист', thumbnail: null, previousVideoId: lastStatusVideoId, until: Date.now() + 8000 };
    updateStatus();
  } catch (e) {
    showToast(e.message || 'Ошибка запуска плейлиста');
  }
}

let ownPlaylistsExpanded = {}; // playlistId -> bool

async function loadOwnPlaylists() {
  const box = document.getElementById('ownPlaylistsBox');
  if (!box) return;
  try {
    const res = await fetch('/api/playlists');
    const data = await res.json();
    if (!res.ok || !data.ok) throw new Error(data.error || 'Плейлисты недоступны');
    box.innerHTML = '';
    const playlists = data.playlists || [];
    if (!playlists.length) {
      box.innerHTML = '<div class="muted" style="font-size:12px;">Своих плейлистов ещё нет. Создай первый и добавляй туда треки кнопкой «+».</div>';
      return;
    }
    playlists.forEach(pl => {
      const wrap = document.createElement('div');
      const head = document.createElement('div');
      head.className = 'row-item';
      head.style.fontWeight = '700';
      const expanded = !!ownPlaylistsExpanded[pl.id];
      head.innerHTML = `
        <button class="btn ghost" style="padding:2px 6px;" title="Развернуть">${icon(expanded ? 'chevron-down' : 'chevron-right')}</button>
        <div class="row-main">
          <span class="row-title">${esc(pl.name)}</span>
          <span class="row-artist">${(pl.tracks || []).length} ${plural((pl.tracks || []).length, 'трек', 'трека', 'треков')}</span>
        </div>
        <div class="row-actions">
          <button class="btn pl-add-btn" title="Добавить текущий трек" style="color:var(--primary);">${icon('plus')}</button>
          <button class="btn pl-play-btn" title="Играть" style="color:var(--primary);">${icon('play')}</button>
          <button class="btn pl-del-btn" title="Удалить плейлист" style="color:var(--red);">${icon('x')}</button>
        </div>
      `;
      head.querySelector('.btn.ghost').onclick = (e) => {
        e.stopPropagation();
        ownPlaylistsExpanded[pl.id] = !expanded;
        loadOwnPlaylists();
      };
      head.querySelector('.pl-add-btn').onclick = (e) => { e.stopPropagation(); addToOwnPlaylist(pl.id, pl.name); };
      head.querySelector('.pl-play-btn').onclick = (e) => { e.stopPropagation(); playOwnPlaylist(pl.id, pl.name); };
      head.querySelector('.pl-del-btn').onclick = (e) => { e.stopPropagation(); deleteOwnPlaylist(pl.id, pl.name); };
      wrap.appendChild(head);

      if (expanded) {
        (pl.tracks || []).forEach(track => {
          const row = document.createElement('div');
          row.className = 'row-item';
          row.style.paddingLeft = '34px';
          row.innerHTML = `
            <div class="row-main">
              <span class="row-title">${esc(track.title || 'Трек')}</span>
              <span class="row-artist">— ${esc(track.artist || '')}</span>
            </div>
            <div class="row-actions">
              <button class="btn pl-track-play" title="Слушать">${icon('play')}</button>
              <button class="btn pl-track-del" title="Убрать из плейлиста" style="color:var(--red);">${icon('x')}</button>
            </div>
          `;
          row.querySelector('.pl-track-play').onclick = (e) => {
            e.stopPropagation();
            sendQuickPick(track.videoId, track.title, track.artist, track.thumbnail);
          };
          row.querySelector('.pl-track-del').onclick = (e) => {
            e.stopPropagation();
            removeFromOwnPlaylist(pl.id, track.videoId);
          };
          wrap.appendChild(row);
        });
        if (!(pl.tracks || []).length) {
          const empty = document.createElement('div');
          empty.className = 'muted';
          empty.style.cssText = 'font-size:11px; padding:4px 34px;';
          empty.textContent = 'Пустой — добавь текущий трек кнопкой «+»';
          wrap.appendChild(empty);
        }
      }
      box.appendChild(wrap);
    });
  } catch (e) {
    box.innerHTML = `<div class="error-text">${esc(e.message || e)}</div>`;
  }
}

/* ============ РАДИОСТАНЦИИ ============ */

async function loadStations() {
  const box = document.getElementById('stationsBox');
  if (!box) return;
  try {
    const res = await fetch('/api/stations');
    const data = await res.json();
    if (!res.ok || !data.ok) throw new Error(data.error || 'Станции недоступны');
    box.innerHTML = '';
    (data.stations || []).forEach(st => {
      const chip = document.createElement('button');
      chip.className = 'station-chip' + (data.active === st.id ? ' active' : '');
      chip.innerHTML = `<span class="st-name">${esc(st.name)}</span><span class="st-genre">${esc(st.genre || '')}</span>`;
      chip.onclick = () => playStation(st.id, st.name);
      box.appendChild(chip);
    });
  } catch (e) {
    box.innerHTML = `<span class="error-text" style="font-size:11px;">${esc(e.message || e)}</span>`;
  }
}

async function playStation(id, name) {
  showToast(`${name}…`);
  try {
    const res = await fetch('/api/station/play', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ id })
    });
    const data = await res.json();
    if (!res.ok || !data.ok) throw new Error(data.error || 'Не удалось запустить станцию');
    lyricsData = null; lyricsVideoId = null;
    setTimeout(() => { updateStatus(); loadStations(); }, 1500);
    updateStatus();
  } catch (e) {
    showToast(e.message || 'Ошибка станции');
  }
}

async function stopStation() {
  try {
    await fetch('/api/station/stop', { method: 'POST' });
    showToast('Станция остановлена — возвращаюсь к очереди');
    setTimeout(() => { updateStatus(); loadStations(); }, 1500);
  } catch (e) {
    showToast(e.message || 'Ошибка');
  }
}


/* ============ СТАТУС ============ */

async function updateStatus() {
  const requestId = ++statusRequestId;
  try {
    const res = await fetch('/api/status');
    const data = await res.json();
    if (requestId !== statusRequestId) return;
    if (!res.ok || !data.track) throw new Error(data.error || 'Статус недоступен');
    activeProfileName = data.active_profile || activeProfileName;
    lastStatusVideoId = data.track.video_id || '';
    const prevVideoId = currentTrackData ? currentTrackData.video_id : '';
    currentTrackData = data.track;

    const likeButton = document.getElementById('likeBtn');
    if (likeButton) {
      const liked = !!data.track.favorite;
      likeButton.classList.toggle('liked', liked);
      likeButton.title = liked ? 'Убрать из избранного' : 'В избранное (L)';
    }

    const pendingActive = pendingTrack && Date.now() < pendingTrack.until;
    const stillOnPrevious = pendingTrack
      ? (pendingTrack.videoId
        ? data.track.video_id !== pendingTrack.videoId
        : data.track.video_id === pendingTrack.previousVideoId)
      : false;
    const showPending = pendingActive && stillOnPrevious;

    if (showPending) {
      document.getElementById('trackTitle').textContent = pendingTrack.title || data.track.title;
      document.getElementById('trackArtist').textContent = pendingTrack.artist || data.track.artist;
      if (pendingTrack.thumbnail) document.getElementById('trackThumb').src = pendingTrack.thumbnail;
      document.getElementById('sourceBadge').textContent = `● ПЕРЕКЛЮЧЕНИЕ...`;
    } else {
      pendingTrack = null;
      document.getElementById('trackTitle').textContent = data.track.title;
      document.getElementById('trackArtist').textContent = data.track.artist;
      let badgeText = data.track.is_station
        ? `РАДИО: ${data.track.status || 'Live'}`
        : `В ЭФИРЕ (${data.track.status || 'Live'})`;
      if (data.track.album && !data.track.is_station) {
        badgeText = `В ЭФИРЕ: Альбом ${data.track.album}`;
      }
      document.getElementById('sourceBadge').textContent = badgeText;
    }
    renderQueue(data.queue, data.track.is_station ? null : data.track.album);

    // Смена трека — сброс текста песни
    if (prevVideoId !== data.track.video_id) {
      lyricsData = null;
      lyricsVideoId = null;
      if (lyricsShown) loadLyrics(true);
    }

    if (!showPending && data.track.thumbnail) {
      const thumbEl = document.getElementById('trackThumb');
      if (thumbEl.getAttribute('src') !== data.track.thumbnail) {
        thumbEl.src = data.track.thumbnail;
      }
    }

    const isPaused = !!data.track.is_paused;
    document.getElementById('playPauseBtn').innerHTML = icon(isPaused ? 'play' : 'pause');

    currentDuration = data.track.duration || 180;
    currentSec = data.track.current_time || 0;

    document.getElementById('timelineWrap').style.display = data.track.is_station ? 'none' : '';
    document.getElementById('currentTime').textContent = formatTime(currentSec);
    document.getElementById('durationTime').textContent = formatTime(currentDuration);
    if (!data.track.is_station) {
      const percent = Math.min(100, (currentSec / currentDuration) * 100);
      document.getElementById('timelineFill').style.width = percent + '%';
    }

    document.getElementById('cacheStatHeader').textContent = data.config && data.config.cache_enabled === false
      ? 'стрим'
      : `${data.cache_mb} МБ (${data.cache_count})`;
    document.getElementById('cacheDetailed').textContent = `${data.cache_count} треков (${data.cache_mb} МБ)`;
    const cachePathLabel = document.getElementById('cachePathLabel');
    if (cachePathLabel && data.cache_dir) cachePathLabel.textContent = data.cache_dir;

    // Статус аккаунта
    const authDot = document.getElementById('headerAuthDot');
    const authLabel = document.getElementById('headerAuthLabel');
    const avatarSmall = document.getElementById('headerUserAvatar');
    const ytmAvatarPhoto = document.getElementById('ytmAvatarPhoto');
    const ytmAvatarInit = document.getElementById('ytmAvatarInitial');
    const ytmUserName = document.getElementById('ytmUserName');

    const currentName = (data.account_info && data.account_info.accountName) ? data.account_info.accountName : data.active_profile;
    ytmUserName.textContent = currentName.toUpperCase();

    authDot.classList.remove('green', 'yellow');
    if (data.is_auth) {
      authDot.classList.add('green');
      authLabel.textContent = currentName;
      if (data.account_info && data.account_info.accountPhotoUrl) {
        avatarSmall.src = data.account_info.accountPhotoUrl;
        avatarSmall.style.display = '';
        ytmAvatarPhoto.src = data.account_info.accountPhotoUrl;
        ytmAvatarPhoto.style.display = 'block';
        ytmAvatarInit.style.display = 'none';
      } else {
        avatarSmall.style.display = 'none';
        ytmAvatarPhoto.style.display = 'none';
        ytmAvatarInit.style.display = 'block';
        ytmAvatarInit.textContent = currentName.charAt(0).toUpperCase();
      }
    } else {
      authDot.classList.add('yellow');
      authLabel.textContent = currentName;
      avatarSmall.style.display = 'none';
      ytmAvatarPhoto.style.display = 'none';
      ytmAvatarInit.style.display = 'block';
      ytmAvatarInit.textContent = currentName.charAt(0).toUpperCase();
    }

    const sig = JSON.stringify([data.profiles, data.active_profile, data.is_auth, data.profile_accounts]);
    if (sig !== lastProfilesSig) {
      lastProfilesSig = sig;
      renderProfilesList(data.profiles, data.active_profile, data.is_auth, data.profile_accounts);
      loadProfileChannels();
    }

    if (data.config) {
      const setIfIdle = (id, val) => {
        const el = document.getElementById(id);
        if (el && document.activeElement !== el && val !== undefined && val !== null) {
          el.value = String(val);
        }
      };
      setIfIdle('settingBitrate', data.config.bitrate);
      const boostSlider = document.getElementById('settingBoostSlider');
      if (boostSlider && document.activeElement !== boostSlider) {
        const pct = Math.round((parseFloat(data.config.volume_boost) || 1) * 100);
        boostSlider.value = String(pct);
        const lbl = document.getElementById('boostLabel');
        if (lbl) lbl.textContent = pct + '%';
      }
      setIfIdle('settingLimit', data.config.cache_limit);
      const rpcToggle = document.getElementById('discordRpcToggle');
      if (rpcToggle && document.activeElement !== rpcToggle) {
        rpcToggle.checked = !!data.config.discord_rpc;
      }
      const cacheOn = !!data.config.cache_enabled;
      const cacheToggleEl = document.getElementById('cacheToggle');
      if (cacheToggleEl && document.activeElement !== cacheToggleEl) cacheToggleEl.checked = cacheOn;
      const cacheControls = document.getElementById('cacheControls');
      if (cacheControls) cacheControls.style.display = cacheOn ? '' : 'none';
    }

    // Подсветка синхро-текста
    if (lyricsShown && lyricsData && lyricsData.synced) syncLyricsHighlight();
  } catch (e) {
    console.error('Status update failed:', e);
    if (requestId === statusRequestId) {
      document.getElementById('sourceBadge').textContent = '● СЕРВЕР НЕДОСТУПЕН';
    }
  }
}

/* ============ ПРОФИЛИ / КАНАЛЫ ============ */

async function loadProfileChannels() {
  const box = document.getElementById('channelList');
  if (!box) return;
  box.innerHTML = '<span class="muted" style="font-size:12px;">Загрузка каналов...</span>';
  try {
    const res = await fetch('/api/profiles/channels', { method: 'POST' });
    const data = await res.json();
    if (!res.ok || !data.ok) throw new Error(data.error || 'Каналы недоступны');
    box.innerHTML = '';
    for (const channel of (data.channels || [])) {
      const button = document.createElement('button');
      button.className = 'btn';
      button.style.cssText = 'padding:5px 8px; font-size:11px;';
      button.textContent = `${channel.name || 'Канал'}${channel.selected ? ' (выбран)' : ''}`;
      button.onclick = () => setProfileChannel(channel.user_id || '');
      box.appendChild(button);
    }
    if (!(data.channels || []).length) {
      const hint = document.createElement('span');
      hint.className = 'muted';
      hint.style.fontSize = '11px';
      hint.textContent = 'Автосписок недоступен — вставь Brand channel ID вручную.';
      box.appendChild(hint);
    }
  } catch (e) {
    box.innerHTML = `<span style="color:var(--yellow); font-size:11px;">${esc(e.message || e)}</span>`;
  }
}

async function setProfileChannel(channelId) {
  try {
    const res = await fetch('/api/profiles/set_channel', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ name: activeProfileName, channel_id: channelId || '' })
    });
    const data = await res.json();
    if (!res.ok || !data.ok) throw new Error(data.error || 'Не удалось применить канал');
    await updateStatus();
    await loadUserPlaylists(true);
    loadQuickPicks(true);
    showToast('Канал профиля обновлён');
  } catch (e) {
    showToast(e.message || 'Ошибка выбора канала');
  }
}

function renderProfilesList(list, active, isAuth, accounts) {
  const box = document.getElementById('profilesList');
  const select = document.getElementById('headerProfileSelect');
  if (!box || !select) return;

  box.innerHTML = '';
  select.innerHTML = '';

  const all = list && list.length > 0 ? [...list] : ["Мой профиль"];
  if (!all.includes("Мой профиль")) all.unshift("Мой профиль");

  all.forEach(name => {
    const isA = name === active;

    const opt = document.createElement('option');
    opt.value = name;
    opt.textContent = isA ? `★ ${name}` : name;
    if (isA) opt.selected = true;
    select.appendChild(opt);

    const card = document.createElement('div');
    card.className = 'row-item';
    card.style.cursor = 'default';

    const left = document.createElement('div');
    left.className = 'row-main';
    const dot = document.createElement('span');
    dot.className = 'auth-dot' + (isA ? (isAuth ? ' green' : ' yellow') : '');
    const nmWrap = document.createElement('div');
    nmWrap.style.cssText = 'display:flex; flex-direction:column; overflow:hidden;';
    const nm = document.createElement('span');
    nm.className = 'row-title';
    nm.textContent = name;
    nmWrap.appendChild(nm);
    const acc = accounts && accounts[name];
    if (acc && acc !== name) {
      const accEl = document.createElement('span');
      accEl.className = 'row-artist';
      accEl.textContent = 'YT: ' + acc;
      nmWrap.appendChild(accEl);
    }
    left.appendChild(dot);
    left.appendChild(nmWrap);
    if (isA) {
      const b = document.createElement('span');
      b.className = 'row-tag';
      b.textContent = isAuth ? 'АКТИВЕН' : 'ЖДЁТ ВХОДА';
      left.appendChild(b);
    }
    const btnBox = document.createElement('div');
    btnBox.className = 'row-actions';
    if (!isA) {
      const choose = document.createElement('button');
      choose.className = 'btn';
      choose.textContent = 'Выбрать';
      choose.onclick = () => switchProfile(name);
      btnBox.appendChild(choose);
    }
    if (name !== "Мой профиль") {
      const del = document.createElement('button');
      del.className = 'btn';
      del.style.color = 'var(--red)';
      del.innerHTML = icon('trash');
      del.onclick = () => deleteProfile(name);
      btnBox.appendChild(del);
    }
    card.appendChild(left);
    card.appendChild(btnBox);
    box.appendChild(card);
  });
}

async function loginWithCookie() {
  const input = document.getElementById('browserCookieInput').value.trim();
  const name = document.getElementById('browserProfileName').value.trim() || "Мой профиль";
  if (!input) {
    alert("Вставь строку Cookie или заголовки!");
    return;
  }
  try {
    const res = await fetch('/api/auth/browser', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ input, name })
    });
    const data = await res.json();
    if (data.ok) {
      if (data.warning) {
        showToast(data.warning);
        alert(data.warning);
      } else {
        showToast(`Подключено! Канал: ${data.real_channel || name} (authuser=${data.authuser ?? '?'})`);
      }
      document.getElementById('browserCookieInput').value = "";
      await updateStatus();
      await loadUserPlaylists(true);
      loadQuickPicks(true);
    } else {
      alert(data.error || "Ошибка авторизации");
    }
  } catch (e) {
    alert("Ошибка связи с сервером");
  }
}

async function importBrowserJson() {
  try {
    const res = await fetch('/api/auth/import_browser_json', { method: 'POST' });
    const data = await res.json();
    if (data.ok) {
      showToast("browser.json успешно импортирован!");
      await updateStatus();
      await loadUserPlaylists(true);
      loadQuickPicks(true);
    } else {
      alert(data.error || "Не удалось импортировать browser.json");
    }
  } catch (e) {
    alert("Ошибка импорта");
  }
}

async function startOAuth() {
  let client_id = document.getElementById('clientIdInput').value.trim();
  let client_secret = document.getElementById('clientSecretInput').value.trim();
  client_id = client_id.replace(/[\u2010-\u2015\u2212–—−]/g, '-').replace(/['"`]/g, '').trim();
  client_secret = client_secret.replace(/[\u2010-\u2015\u2212–—−]/g, '-').replace(/['"`]/g, '').trim();
  const custom_name = document.getElementById('oauthNameInput').value.trim() || "Мой профиль";

  try {
    const res = await fetch('/api/oauth/start', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ client_id, client_secret, custom_name })
    });
    const data = await res.json();
    if (!data.ok) {
      alert(data.error);
      return;
    }
    document.getElementById('oauthBox').style.display = 'block';
    document.getElementById('oauthCode').textContent = data.user_code;
    document.getElementById('oauthUrl').href = data.verification_url;
    document.getElementById('oauthUrl').textContent = data.verification_url;
    document.getElementById('oauthStatusText').textContent = "Ожидание подтверждения в браузере...";
    window.open(data.verification_url, '_blank');
    if (oauthPollTimer) clearInterval(oauthPollTimer);
    oauthPollIntervalSec = data.interval || 5;
    oauthPollTimer = setInterval(pollOAuthStatus, oauthPollIntervalSec * 1000);
  } catch (e) {
    alert("Ошибка связи с сервером.");
  }
}

async function pollOAuthStatus() {
  try {
    const res = await fetch('/api/oauth/poll', { method: 'POST' });
    const data = await res.json();
    if (data.status === 'success') {
      clearInterval(oauthPollTimer);
      oauthPollTimer = null;
      document.getElementById('oauthBox').style.display = 'none';
      showToast(`Готово! Профиль "${data.profile_name}" подключён!`);
      await updateStatus();
      await loadUserPlaylists(true);
      loadQuickPicks(true);
    } else if (data.status === 'error') {
      clearInterval(oauthPollTimer);
      oauthPollTimer = null;
      document.getElementById('oauthStatusText').textContent = "Ошибка: " + data.error;
    }
    // Google попросил замедлиться — растягиваем интервал на 5 секунд
    if (data.slow_down && oauthPollTimer) {
      clearInterval(oauthPollTimer);
      oauthPollIntervalSec += 5;
      oauthPollTimer = setInterval(pollOAuthStatus, oauthPollIntervalSec * 1000);
    }
  } catch (e) {}
}

async function switchProfile(name) {
  showToast(`Переключаюсь на: ${name}`);
  const res = await fetch('/api/profiles/switch', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ name })
  });
  const data = await res.json();
  if (!res.ok || !data.ok) throw new Error(data.error || 'Не удалось переключить профиль');
  await updateStatus();
  await loadUserPlaylists(true);
  loadQuickPicks(true);
  loadOwnPlaylists();
}

async function deleteProfile(name) {
  if (!confirm(`Удалить профиль "${name}"?`)) return;
  const res = await fetch('/api/profiles/delete', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ name })
  });
  const data = await res.json();
  if (!res.ok || !data.ok) throw new Error(data.error || 'Не удалось удалить профиль');
  await updateStatus();
  await loadUserPlaylists(true);
  loadQuickPicks(true);
}

/* ============ НАСТРОЙКИ / КЕШ ============ */

async function updateSettings() {
  try {
    const bitrate = document.getElementById('settingBitrate').value;
    const volume_boost = (parseInt(document.getElementById('settingBoostSlider').value, 10) || 100) / 100;
    const cache_limit = document.getElementById('settingLimit').value;
    const discord_rpc = document.getElementById('discordRpcToggle').checked;
    const cache_enabled = document.getElementById('cacheToggle').checked;

    const res = await fetch('/api/config', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ bitrate, volume_boost, cache_limit, discord_rpc, cache_enabled })
    });
    const data = await res.json();
    if (!res.ok || !data.ok) throw new Error(data.error || 'Не удалось сохранить настройки');
    showToast('Настройки применены');
    updateStatus();
  } catch (e) {
    showToast(e.message || 'Ошибка настроек');
  }
}

async function clearCacheNow(clearAll = false) {
  if (!clearAll && !confirm("Очистить старые треки из кеша?")) return;
  if (clearAll && !confirm("Удалить ВСЕ файлы кеша?")) return;
  try {
    const endpoint = clearAll ? '/api/cache/clear' : '/api/cache/clean';
    const options = { method: 'POST', headers: { 'Content-Type': 'application/json' } };
    if (!clearAll) options.body = JSON.stringify({ max_age_days: 30 });
    const res = await fetch(endpoint, options);
    const data = await res.json();
    if (!res.ok || !data.ok) throw new Error(data.error || 'Не удалось очистить кеш');
    showToast(clearAll ? `Очищено файлов: ${data.deleted}` : 'Старые файлы очищены');
    updateStatus();
  } catch (e) {
    showToast(e.message || 'Ошибка очистки кеша');
  }
}

function cleanCacheNow() {
  return clearCacheNow(false);
}

// Радио в игре: проверка/прописывание в live_streams.sii
async function checkEts2Stream() {
  const el = document.getElementById('ets2StatusText');
  if (!el) return;
  try {
    const res = await fetch('/api/ets2/check');
    const data = await res.json();
    if (!data.found_any) {
      el.textContent = 'Игра не найдена в Documents (ETS2/ATS). Пропиши вручную или запусти игру один раз.';
      return;
    }
    if (data.installed_any) {
      const p = data.all.find(f => f.exists && f.has_stream);
      el.textContent = 'Радио уже прописано в ' + p.game + ' (' + p.path + ')';
    } else {
      el.textContent = 'Радио в live_streams.sii не прописано — нажми «Прописать»';
    }
  } catch (e) {
    el.textContent = 'Не удалось проверить игру';
  }
}

async function ets2InstallStream() {
  const el = document.getElementById('ets2StatusText');
  try {
    const res = await fetch('/api/ets2/install_stream', { method: 'POST' });
    const data = await res.json();
    if (!res.ok || !data.ok) throw new Error(data.error || 'Не удалось прописать');
    showToast('Радио прописано в live_streams.sii (бэкап .bak рядом)');
    checkEts2Stream();
  } catch (e) {
    showToast(e.message || 'Ошибка прописывания');
    if (el) el.textContent = 'Ошибка: ' + (e.message || e);
  }
}

/* ============ ГЛОБАЛЬНЫЕ ХОТКЕИ / ИНИЦ ============ */

// Любой fetch/await без своего catch не остаётся молчаливым — показываем тост.
window.addEventListener('unhandledrejection', (ev) => {
  const reason = ev.reason;
  const msg = reason && reason.message ? reason.message : 'Запрос не удался';
  showToast('Ошибка: ' + msg);
});

// Подтягиваем сохранённые OAuth-креды (вводятся один раз навсегда)
async function loadOAuthClient() {
  try {
    const res = await fetch('/api/oauth/client');
    const data = await res.json();
    if (data.client_id) document.getElementById('clientIdInput').value = data.client_id;
    if (data.client_secret) document.getElementById('clientSecretInput').value = data.client_secret;
    const note = document.getElementById('oauthCredsNote');
    if (note) note.style.display = data.configured ? 'none' : 'block';
  } catch (e) {}
}

document.addEventListener('keydown', (e) => {
  const target = e.target;
  if (target && (target.tagName === 'INPUT' || target.tagName === 'TEXTAREA' || target.tagName === 'SELECT' || target.tagName === 'BUTTON' || target.tagName === 'A' || target.isContentEditable)) return;
  if (e.ctrlKey || e.altKey || e.metaKey) return;
  if (e.repeat) return; // зажатый Space не должен спамить паузой
  if (e.code === 'Space') { e.preventDefault(); togglePause(); }
  else if (e.key === 'ArrowRight') { e.preventDefault(); skipTrack(); }
  else if (e.key === 'ArrowLeft') { e.preventDefault(); previousTrack(); }
  else if (e.code === 'KeyL') { e.preventDefault(); toggleFavoriteCurrent(); }
  else if (e.code === 'KeyD') { e.preventDefault(); dislikeCurrentTrack(); }
  else if (e.code === 'KeyY') { e.preventDefault(); toggleLyrics(); }
});

updateStatus().then(() => {
  loadQuickPicks();
  loadUserPlaylists();
  loadLibrary('history');
  loadOwnPlaylists();
  loadStations();
  loadOAuthClient();
  checkEts2Stream();
});
setInterval(updateStatus, 1000);
