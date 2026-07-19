/* טבלת התאמות — front-end logic.
   Talks to the Python side through window.pywebview.api (the Api class in
   src/gui_api.py). All heavy lifting is Python; this file is UI only. */

'use strict';

// Bump on every UI change. Shown in the header so we can confirm, from a
// screenshot, exactly which version is running (stale files are the #1 gotcha).
const BUILD = 14;

const $ = (sel, root = document) => root.querySelector(sel);
const api = () => window.pywebview.api;

let hoverClass = null;   // class card the pointer is currently over (for drops)
let running = false;
let classNames = new Set();  // existing class names, for live duplicate checks
let moveDrag = null;     // {cls, file} while a file chip is dragged between cards

// ---- boot ---------------------------------------------------------------
// Surface any error instead of letting the UI hang silently. A frozen-looking
// app is almost always an uncaught error or a rejected bridge promise.
window.addEventListener('error', e => toast('שגיאה: ' + (e.message || e.error), 'err'));
window.addEventListener('unhandledrejection', e => {
  const r = e.reason;
  toast('שגיאה: ' + (r && r.message ? r.message : String(r)), 'err');
});

window.addEventListener('pywebviewready', init);
// Fallback if the event already fired before this script ran.
if (window.pywebview && window.pywebview.api) init();
// Last-resort: if neither path ran within 3s, the bridge never arrived.
setTimeout(() => {
  if (!init.done && (!window.pywebview || !window.pywebview.api)) {
    toast('הגשר אל התוכנה לא נטען — נסו לפתוח מחדש', 'err');
  }
}, 3000);

async function init() {
  if (init.done) return;
  init.done = true;
  console.log('GUI build', BUILD);
  const b = $('#build'); if (b) b.textContent = 'v' + BUILD;
  try {
    bindChrome();
    await refresh();
    await refreshKeyStatus();
  } catch (err) {
    toast('שגיאה בטעינה: ' + (err.message || err), 'err');
  }
}

// ---- rendering ----------------------------------------------------------
async function refresh() {
  const classes = await api().list_classes();
  classNames = new Set(classes.map(c => c.name));
  const board = $('#board');
  board.innerHTML = '';
  $('#empty').hidden = classes.length > 0;
  classes.forEach((c, i) => board.appendChild(renderCard(c, i)));
  $('#generate').disabled = classes.length === 0;
  classes.forEach(c => loadThumbs(c.name));
  refreshEstimate();
}

// "12 אבחונים · כ־7 דקות" beside the run button — learned from past runs.
async function refreshEstimate() {
  if (running) return;
  try {
    const e = await api().run_estimate();
    const rs = $('#runStatus');
    if (!e || !e.files) { rs.textContent = ''; return; }
    const mins = Math.max(1, Math.round(e.seconds / 60));
    const t = e.seconds < 75 ? 'כדקה' : `כ־${mins} דקות`;
    rs.textContent = `${e.files} אבחונים · זמן משוער: ${t}`;
  } catch { /* estimate is decoration; never block the UI on it */ }
}

// Page-1 thumbnails arrive lazily (cached on disk after the first render).
async function loadThumbs(className) {
  try {
    const r = await api().thumbnails(className);
    if (!r || !r.ok) return;
    const card = $(`.card[data-class="${cssEsc(className)}"]`);
    if (!card) return;
    card.querySelectorAll('.file').forEach(row => {
      const src = r.thumbs[row.dataset.file];
      if (src) $('.fico', row).innerHTML = `<img class="fthumb" src="${src}" alt="" />`;
    });
  } catch { /* keep the plain icon */ }
}

function renderCard(c, index) {
  const card = el('section', 'card');
  card.style.animationDelay = `${Math.min(index * 60, 360)}ms`;
  card.dataset.class = c.name;

  card.innerHTML = `
    <div class="card-head">
      <span class="class-badge">${esc(c.name)}</span>
      <span class="count"><b>${c.count}</b> אבחונים</span>
      <button class="icon-btn trash" title="מחק כיתה">🗑</button>
    </div>
    <div class="teacher-row">
      <label>מחנכת:</label>
      <input class="teacher-input" type="text" name="teacher-${esc(c.name)}"
             aria-label="שם המחנכת של כיתה ${esc(c.name)}"
             placeholder="שם המחנכת" value="${esc(c.teacher)}" />
    </div>
    <div class="drop" title="לחצו כדי לבחור קבצים, או גררו לכאן">
      <span class="plus">＋</span>
      גררו לכאן אבחונים או לחצו לבחירה
      <div style="font-size:12px;color:var(--ink-faint);margin-top:4px">PDF · תמונה · צילום מסך</div>
    </div>
    <div class="files"></div>
    <div class="card-foot">
      <button class="btn btn-ghost btn-mini open">📂 תיקייה</button>
    </div>
  `;

  renderFiles(card, c);

  // teacher name — save on blur / Enter (debounced idea kept simple: save on change)
  const teacher = $('.teacher-input', card);
  const saveTeacher = async () => { await api().set_teacher(c.name, teacher.value); };
  teacher.addEventListener('change', saveTeacher);
  teacher.addEventListener('blur', saveTeacher);

  $('.trash', card).addEventListener('click', () => confirmDeleteClass(c.name));
  $('.open', card).addEventListener('click', () => api().open_class_folder(c.name));

  const drop = $('.drop', card);
  drop.addEventListener('click', () => pickInto(c.name));

  // drag visuals + drop
  card.addEventListener('dragenter', e => { e.preventDefault(); hoverClass = c.name; card.classList.add('drag'); });
  card.addEventListener('dragover', e => { e.preventDefault(); });
  card.addEventListener('dragleave', e => { if (!card.contains(e.relatedTarget)) card.classList.remove('drag'); });
  card.addEventListener('drop', e => {
    e.preventDefault();
    card.classList.remove('drag');
    // A chip dragged from another card is an internal move — handle it here.
    if (moveDrag && moveDrag.cls !== c.name) {
      const m = moveDrag;
      moveDrag = null;
      moveFile(m.cls, m.file, c.name);
      return;
    }
    // Real file paths are resolved by the Python drop handler, which calls
    // window.onFilesDropped(); it routes to whichever card is under the cursor
    // (tracked via hoverClass). Nothing to do here but keep hoverClass current.
    hoverClass = c.name;
  });

  return card;
}

function renderFiles(card, c) {
  const box = $('.files', card);
  box.innerHTML = '';
  c.files.forEach(f => {
    const row = el('div', 'file');
    row.dataset.file = f.name;
    row.draggable = true;
    row.title = 'אפשר לגרור לכיתה אחרת';
    row.innerHTML = `
      <span class="fico" title="פתח את הקובץ">${f.is_pdf ? '📄' : '🖼'}</span>
      <span class="fname" title="${esc(f.name)}">${esc(f.name)}</span>
      <span class="fsize">${f.size_kb}KB</span>
      <button class="fx" title="הסר">✕</button>`;
    $('.fico', row).addEventListener('click', () => api().open_source_file(c.name, f.name));
    $('.fx', row).addEventListener('click', async () => {
      const r = await api().remove_file(c.name, f.name);
      if (!r.ok) return toast(r.error || 'שגיאה בהסרה', 'err');
      updateCard(r.class);
      toast(`«${f.name}» הוסר`, '', { label: 'ביטול', fn: () => undoDelete(r.undo) });
    });
    // Drag a chip onto another class card to reassign the diagnostic.
    row.addEventListener('dragstart', e => {
      moveDrag = { cls: c.name, file: f.name };
      row.classList.add('dragging');
      e.dataTransfer.effectAllowed = 'move';
      try { e.dataTransfer.setData('text/plain', f.name); } catch { }
    });
    row.addEventListener('dragend', () => { moveDrag = null; row.classList.remove('dragging'); });
    box.appendChild(row);
  });
  $('.count', card).innerHTML = `<b>${c.count}</b> אבחונים`;
}

function updateCard(c) {
  const card = $(`.card[data-class="${cssEsc(c.name)}"]`);
  if (card) { renderFiles(card, c); loadThumbs(c.name); }
}

async function undoDelete(token) {
  const u = await api().undo_delete(token);
  if (!u.ok) return toast(u.error || 'השחזור נכשל', 'err');
  await refresh();
  toast('שוחזר ✓');
}

async function moveFile(srcCls, file, dstCls) {
  const r = await api().move_file(srcCls, file, dstCls);
  if (!r.ok) return toast(r.error || 'ההעברה נכשלה', 'warn');
  updateCard(r.src);
  updateCard(r.dst);
  toast(`«${file}» עבר לכיתה ${dstCls}`);
}

// ---- file adding --------------------------------------------------------
async function pickInto(className) {
  const paths = await api().pick_files();
  if (paths && paths.length) await addInto(className, paths);
}

async function addInto(className, paths) {
  const res = await api().add_files(className, paths);
  if (!res.ok) return toast(res.error || 'שגיאה בהוספה', 'err');
  updateCard(res.class);
  refreshEstimate();
  const bits = [];
  if (res.added.length) bits.push(`${res.added.length} נוספו`);
  if (res.converted.length) bits.push(`${res.converted.length} תמונות הומרו ל-PDF`);
  if (res.skipped.length) {
    // Say WHY each skipped file was skipped ("already in class ג2"), not just a count.
    const why = res.skipped.slice(0, 2).map(s => `«${s.name}» — ${s.why}`).join('\n');
    const more = res.skipped.length > 2 ? `\nועוד ${res.skipped.length - 2}…` : '';
    bits.push('\n' + why + more);
  }
  toast(bits.join(' · ') || 'לא נוספו קבצים', res.skipped.length ? 'warn' : '');
}

// pywebview exposes the real path on dropped files as pywebviewFullPath.
function extractPaths(e) {
  const out = [];
  const files = e.dataTransfer && e.dataTransfer.files;
  if (files) for (const f of files) if (f.pywebviewFullPath) out.push(f.pywebviewFullPath);
  return out;
}
// Python-side DOM drop handler (if wired) calls this with resolved paths.
window.onFilesDropped = function (paths) {
  if (hoverClass && paths && paths.length) addInto(hoverClass, paths);
};

// ---- create / delete class ---------------------------------------------
function bindChrome() {
  $('#addClass').addEventListener('click', openModal);
  $('#modalCancel').addEventListener('click', closeModal);
  $('#modalScrim').addEventListener('click', e => { if (e.target.id === 'modalScrim') closeModal(); });
  $('#modalCreate').addEventListener('click', doCreate);
  $('#className').addEventListener('input', liveCheckName);
  $('#className').addEventListener('keydown', e => { if (e.key === 'Enter') doCreate(); if (e.key === 'Escape') closeModal(); });
  $('#openOutput').addEventListener('click', () => api().open_output_folder());
  $('#openOutput2').addEventListener('click', () => api().open_output_folder());
  $('#openReport').addEventListener('click', () => api().open_review_report());
  $('#generate').addEventListener('click', generate);
  $('#drawerClose').addEventListener('click', closeDrawer);
  // API key dialog
  $('#openKey').addEventListener('click', openKeyModal);
  $('#bannerSetKey').addEventListener('click', openKeyModal);
  $('#keyClose').addEventListener('click', () => { $('#keyScrim').hidden = true; });
  $('#keyScrim').addEventListener('click', e => { if (e.target.id === 'keyScrim') $('#keyScrim').hidden = true; });
  $('#keyReveal').addEventListener('click', () => {
    const f = $('#apiKey'); f.type = f.type === 'password' ? 'text' : 'password';
  });
  $('#keySave').addEventListener('click', saveKey);
  $('#keyClear').addEventListener('click', clearKey);
  $('#apiKey').addEventListener('keydown', e => { if (e.key === 'Enter') saveKey(); });
}

// ---- API key ------------------------------------------------------------
async function refreshKeyStatus() {
  const s = await api().api_key_status();
  const dot = $('#keyDot');
  dot.className = 'key-dot ' + (s.configured ? 'ok' : 'missing');
  dot.title = s.configured ? 'מפתח מוגדר' : 'לא הוגדר מפתח';
  $('#keyBanner').hidden = s.configured;
  return s;
}

function openKeyModal() {
  $('#apiKey').value = '';
  $('#apiKey').type = 'password';
  const st = $('#keyStatus');
  api().api_key_status().then(s => {
    if (s.source === 'env') {
      st.className = 'key-status ok';
      st.textContent = 'מפתח מוגדר דרך משתנה סביבה (CLAUDE_API_KEY).';
    } else if (s.source === 'saved') {
      st.className = 'key-status ok';
      st.textContent = 'מפתח שמור במחשב זה' + (s.scheme === 'dpapi' ? ' (מוצפן).' : '.');
    } else {
      st.className = 'key-status';
      st.textContent = 'עדיין לא הוגדר מפתח.';
    }
  });
  $('#keyScrim').hidden = false;
  setTimeout(() => $('#apiKey').focus(), 30);
}

async function saveKey() {
  const key = $('#apiKey').value.trim();
  const st = $('#keyStatus');
  const r = await api().save_api_key(key);
  if (!r.ok) { st.className = 'key-status err'; st.textContent = r.error; return; }
  st.className = 'key-status'; st.textContent = 'בודק את המפתח…';
  const t = await api().test_api_key();
  if (t.ok) {
    st.className = 'key-status ok'; st.textContent = '✓ המפתח נשמר ואומת בהצלחה.';
    toast('המפתח נשמר ואומת ✓');
    await refreshKeyStatus();
    setTimeout(() => { $('#keyScrim').hidden = true; }, 900);
  } else {
    st.className = 'key-status err';
    st.textContent = 'נשמר, אך האימות נכשל: ' + t.error;
    await refreshKeyStatus();
  }
}

async function clearKey() {
  if (!window.confirm('למחוק את המפתח השמור?')) return;
  await api().clear_api_key();
  $('#apiKey').value = '';
  const st = $('#keyStatus'); st.className = 'key-status'; st.textContent = 'המפתח נמחק.';
  await refreshKeyStatus();
}

function openModal() {
  const e = $('#modalError');
  e.hidden = true;
  e.className = 'modal-error';
  $('#modalCreate').disabled = false;
  $('#className').value = '';
  $('#modalScrim').hidden = false;
  setTimeout(() => $('#className').focus(), 30);
}
function closeModal() { $('#modalScrim').hidden = true; }

// Informational hint as the teacher types if the class already exists.
// (The button stays enabled — clicking create always closes the dialog, so it
//  can never feel stuck; a duplicate just points at the existing card.)
function liveCheckName() {
  const name = $('#className').value.trim();
  const e = $('#modalError');
  if (name && classNames.has(name)) {
    e.textContent = `כיתה ${name} כבר קיימת — כבר מופיעה ברשימה`;
    e.className = 'modal-error hint';
    e.hidden = false;
  } else {
    e.hidden = true;
  }
}

async function doCreate() {
  const name = $('#className').value.trim();
  if (!name) return;

  // Already exists → don't refuse silently; close and point at it.
  if (classNames.has(name)) {
    closeModal();
    toast(`כיתה ${name} כבר קיימת`, 'warn');
    highlightCard(name);
    return;
  }

  const r = await api().create_class(name);
  closeModal();                          // always close on a create attempt
  if (!r.ok) { toast(r.error || 'שם כיתה לא תקין', 'warn'); return; }
  await refresh();
  toast(`הכיתה «${name}» נוצרה`);
  highlightCard(name);
}

function highlightCard(name) {
  const card = $(`.card[data-class="${cssEsc(name)}"]`);
  if (!card) return;
  card.scrollIntoView({ behavior: 'smooth', block: 'center' });
  card.classList.add('flash');
  setTimeout(() => card.classList.remove('flash'), 1300);
}

// No confirm dialog — deleting moves the class to a trash folder, and the
// toast offers ביטול for a few seconds. Mis-clicks are fully recoverable.
async function confirmDeleteClass(name) {
  const r = await api().delete_class(name);
  if (!r.ok) return toast(r.error || 'שגיאה במחיקה', 'err');
  await refresh();
  toast(`כיתה ${name} נמחקה`, 'warn', { label: 'ביטול', fn: () => undoDelete(r.undo) });
}

// ---- generate + progress drawer ----------------------------------------
async function generate() {
  if (running) return;
  const r = await api().generate();
  if (!r.ok) {
    if (r.error === 'no_api_key') {
      toast('כדי להפיק דוחות צריך מפתח API', 'warn');
      openKeyModal();
      return;
    }
    return toast(r.error || 'לא ניתן להפעיל', 'warn');
  }
  running = true;
  openDrawer();
}

function openDrawer() {
  $('#log').innerHTML = '';
  $('#results').hidden = true;
  $('#results').innerHTML = '';
  $('#drawerFoot').hidden = true;
  $('#drawerClose').hidden = true;
  $('#checklist').hidden = true;
  $('#checklist').innerHTML = '';
  checkTotal = 0; checkDone = 0;
  $('#drawerTitle').textContent = 'מפיק דוחות…';
  $('#progressFill').className = 'progress-fill indeterminate';
  $('#progressFill').style.width = '';
  $('#scrim').hidden = false;
  $('#drawer').hidden = false;
  $('#runStatus').textContent = 'מפיק דוחות…';
}
function closeDrawer() {
  $('#scrim').hidden = true;
  $('#drawer').hidden = true;
  $('#runStatus').textContent = '';
  refreshEstimate();
}

// ---- live checklist: every diagnostic, pending → running → ✓ child's name --
let checkTotal = 0, checkDone = 0;

const chkKey = (cls, file) => `${cls}|${file}`;

function buildChecklist(classes) {
  const box = $('#checklist');
  box.innerHTML = '';
  checkTotal = 0; checkDone = 0;
  classes.forEach(c => {
    const head = el('div', 'chk-class');
    head.textContent = `כיתה ${c.class}`;
    box.appendChild(head);
    c.files.forEach(f => {
      checkTotal++;
      const row = el('div', 'chk-row');
      row.dataset.key = chkKey(c.class, f);
      row.innerHTML = `<span class="chk-ico">•</span><span class="chk-name">${esc(f)}</span>`;
      box.appendChild(row);
    });
  });
  box.hidden = checkTotal === 0;
  if (checkTotal) {
    $('#progressFill').className = 'progress-fill';
    setRunProgress();
  }
}

function setRunProgress() {
  $('#progressFill').style.width = `${checkTotal ? Math.max(3, 100 * checkDone / checkTotal) : 0}%`;
  $('#runStatus').textContent = `מפיק דוחות… ${checkDone} מתוך ${checkTotal}`;
}

function markChecklist(ev) {
  const row = $(`#checklist .chk-row[data-key="${cssEsc(chkKey(ev.class, ev.file))}"]`);
  if (!row) return;
  const ico = $('.chk-ico', row);
  if (ev.type === 'file_start') {
    row.className = 'chk-row run';
    ico.innerHTML = '<span class="chk-spin"></span>';
    row.scrollIntoView({ block: 'nearest', behavior: 'smooth' });
    return;
  }
  checkDone++;
  if (ev.ok) {
    row.className = 'chk-row ok';
    ico.textContent = '✓';
    // The file resolved into a child — show who was found.
    if (ev.student) {
      $('.chk-name', row).textContent = ev.student;
      $('.chk-name', row).title = ev.file;
    }
  } else {
    row.className = 'chk-row fail';
    ico.textContent = '✕';
    row.title = ev.error || '';
  }
  setRunProgress();
}

window.onProgress = function (ev) {
  if (ev.type === 'log') addLogLine(ev.line);
  else if (ev.type === 'plan') buildChecklist(ev.classes);
  else if (ev.type === 'file_start' || ev.type === 'file_done') markChecklist(ev);
};

function addLogLine(line) {
  const t = String(line).trim();
  if (!t) return;
  // Keep the log to progress a teacher cares about; drop technical noise
  // (page counts, token usage, cache notes). Details live in the report.
  if (/page\(s\)|cached, no API|OCR page|in \/|tokens|Prompt cache/i.test(t)) return;
  const div = el('div', 'line');
  if (/FAILED|שגיאה|Error/i.test(t)) div.classList.add('err');
  else if (/⚠|!|חסר|למזג|merge|missing/i.test(t)) div.classList.add('warn');
  else if (/->|wrote|✓|דוח/.test(t)) div.classList.add('ok');
  div.textContent = t;
  const log = $('#log');
  log.appendChild(div);
  log.scrollTop = log.scrollHeight;
}

window.onDone = function (res) {
  running = false;
  $('#progressFill').className = 'progress-fill';
  $('#progressFill').style.width = '100%';
  $('#drawerClose').hidden = false;
  $('#runStatus').textContent = '';

  if (!res.ok) {
    $('#drawerTitle').textContent = 'לא הופקו דוחות';
    addLogLine('שגיאה: ' + (res.error || 'לא ידועה'));
    return;
  }
  $('#drawerTitle').textContent = 'הדוחות מוכנים ✓';
  renderResults(res);
  $('#openReport').hidden = !res.has_report;
  $('#drawerFoot').hidden = false;
  confetti();
  refresh();
};

// A short celebratory burst when the reports are ready. Skipped for teachers
// who asked Windows to reduce motion.
function confetti() {
  if (matchMedia('(prefers-reduced-motion: reduce)').matches) return;
  const cv = el('canvas', 'confetti');
  document.body.appendChild(cv);
  cv.width = innerWidth; cv.height = innerHeight;
  const ctx = cv.getContext('2d');
  const colors = ['#2f8f7f', '#e0a444', '#d97b6e', '#7ba7d9', '#8f6fb8', '#4bae9c'];
  const bits = Array.from({ length: 140 }, (_, i) => ({
    x: Math.random() * cv.width,
    y: -20 - Math.random() * cv.height * 0.35,
    w: 5 + Math.random() * 5,
    c: colors[i % colors.length],
    vx: -1.2 + Math.random() * 2.4,
    vy: 2 + Math.random() * 3.2,
    rot: Math.random() * Math.PI,
    vr: -0.12 + Math.random() * 0.24,
  }));
  const t0 = performance.now();
  const life = 2600;
  (function tick() {
    const dt = performance.now() - t0;
    ctx.clearRect(0, 0, cv.width, cv.height);
    ctx.globalAlpha = Math.max(0, 1 - dt / life);
    for (const p of bits) {
      p.x += p.vx; p.y += p.vy; p.vy += 0.05; p.rot += p.vr;
      ctx.save();
      ctx.translate(p.x, p.y);
      ctx.rotate(p.rot);
      ctx.fillStyle = p.c;
      ctx.fillRect(-p.w / 2, -p.w / 2, p.w, p.w * 0.6);
      ctx.restore();
    }
    if (dt < life) requestAnimationFrame(tick); else cv.remove();
  })();
}

const BAND_ICON = { green: '✓', amber: '●', red: '⚠' };

function renderResults(res) {
  const box = $('#results');
  box.innerHTML = '';

  // Legend — the whole point is deciding by colour at a glance.
  const legend = el('div', 'legend-row');
  legend.innerHTML =
    '<span class="lg green">🟢 תקין</span>' +
    '<span class="lg amber">🟡 כדאי להעיף מבט</span>' +
    '<span class="lg red">🔴 מומלץ לבדוק היטב מול הקובץ</span>';
  box.appendChild(legend);

  (res.classes || []).forEach(c => {
    const card = el('div', 'res-card');
    let html = `<h3>כיתה ${esc(c.class)} <span style="color:var(--ink-faint);font-weight:600">· ${(c.students || []).length} תלמידים</span></h3>`;
    if (c.docx) html += `<div class="r-sub">${esc(c.docx)}</div>`;
    (Object.entries(c.merge || {})).forEach(([name, n]) => {
      html += `<div class="flag merge">🔗 ${n} אבחונים ל-${esc(name)} — שורות סמוכות, למזג ידנית</div>`;
    });
    // Worst-first, so the rows needing attention sit at the top.
    const students = (c.students || []).slice().sort((a, b) => a.score - b.score);
    students.forEach(s => {
      const reasons = (s.reasons || []).map(esc).join(' · ');
      html += `<div class="rline band-${esc(s.band)}" ${reasons ? `title="${reasons}"` : ''}>
        <span class="r-name">${esc(s.name)}</span>
        <div class="ruler"><div class="ruler-mark" style="left:${Math.max(2, Math.min(98, s.score))}%"></div></div>
        <span class="r-icon">${BAND_ICON[s.band] || ''}</span>
        <button type="button" class="btn btn-ghost btn-mini open-src"
                data-class="${esc(c.class)}" data-file="${esc(s.file)}" title="פתח את הקובץ המקורי">📄</button>
      </div>`;
    });
    (c.failures || []).forEach(f => {
      html += `<div class="flag fail">✕ נכשל: ${esc(f)}</div>`;
    });
    card.innerHTML = html;
    box.appendChild(card);
  });
  box.querySelectorAll('.open-src').forEach(btn => {
    btn.addEventListener('click', () =>
      api().open_source_file(btn.dataset.class, btn.dataset.file));
  });
  box.hidden = false;
}

// ---- helpers ------------------------------------------------------------
function el(tag, cls) { const n = document.createElement(tag); if (cls) n.className = cls; return n; }
function esc(s) { return String(s == null ? '' : s).replace(/[&<>"]/g, m => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[m])); }
function cssEsc(s) { return String(s).replace(/["\\]/g, '\\$&'); }

let toastTimer;
function toast(msg, kind = '', action = null) {
  const t = $('#toast');
  t.textContent = msg;
  if (action) {
    const b = el('button', 'toast-btn');
    b.type = 'button';
    b.textContent = action.label;
    b.addEventListener('click', () => {
      t.hidden = true;
      clearTimeout(toastTimer);
      action.fn();
    });
    t.appendChild(b);
  }
  t.className = 'toast' + (kind ? ' ' + kind : '');
  t.hidden = false;
  clearTimeout(toastTimer);
  // A toast carrying an undo button lingers longer, so it can actually be hit.
  toastTimer = setTimeout(() => { t.hidden = true; }, action ? 7000 : 3200);
}
