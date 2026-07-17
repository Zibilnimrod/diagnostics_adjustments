/* טבלת התאמות — front-end logic.
   Talks to the Python side through window.pywebview.api (the Api class in
   src/gui_api.py). All heavy lifting is Python; this file is UI only. */

'use strict';

const $ = (sel, root = document) => root.querySelector(sel);
const api = () => window.pywebview.api;

let hoverClass = null;   // class card the pointer is currently over (for drops)
let running = false;
let classNames = new Set();  // existing class names, for live duplicate checks

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
  try {
    bindChrome();
    await refresh();
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
      <input class="teacher-input" type="text" placeholder="שם המחנכת" value="${esc(c.teacher)}" />
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
  card.addEventListener('drop', async e => {
    e.preventDefault();
    card.classList.remove('drag');
    const paths = extractPaths(e);
    if (paths.length) await addInto(c.name, paths);
    else pickInto(c.name);   // paths not exposed by the webview → open picker
  });

  return card;
}

function renderFiles(card, c) {
  const box = $('.files', card);
  box.innerHTML = '';
  c.files.forEach(f => {
    const row = el('div', 'file');
    row.innerHTML = `
      <span class="fico">${f.is_pdf ? '📄' : '🖼'}</span>
      <span class="fname" title="${esc(f.name)}">${esc(f.name)}</span>
      <span class="fsize">${f.size_kb}KB</span>
      <button class="fx" title="הסר">✕</button>`;
    $('.fx', row).addEventListener('click', async () => {
      const r = await api().remove_file(c.name, f.name);
      if (r.ok) updateCard(r.class);
    });
    box.appendChild(row);
  });
  $('.count', card).innerHTML = `<b>${c.count}</b> אבחונים`;
}

function updateCard(c) {
  const card = $(`.card[data-class="${cssEsc(c.name)}"]`);
  if (card) renderFiles(card, c);
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
  const bits = [];
  if (res.added.length) bits.push(`${res.added.length} נוספו`);
  if (res.converted.length) bits.push(`${res.converted.length} תמונות הומרו ל-PDF`);
  if (res.skipped.length) bits.push(`${res.skipped.length} דולגו (כפילויות)`);
  toast(bits.join(' · ') || 'לא נוספו קבצים', res.skipped.length && !res.added.length ? 'warn' : '');
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
  $('#generate').addEventListener('click', generate);
  $('#drawerClose').addEventListener('click', closeDrawer);
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

// Warn as the teacher types if the class already exists — before they click.
function liveCheckName() {
  const name = $('#className').value.trim();
  const e = $('#modalError');
  if (name && classNames.has(name)) {
    e.textContent = `כיתה ${name} כבר קיימת`;
    e.className = 'modal-error hint';
    e.hidden = false;
    $('#modalCreate').disabled = true;
  } else {
    e.hidden = true;
    $('#modalCreate').disabled = false;
  }
}

async function doCreate() {
  const name = $('#className').value.trim();
  if (!name || classNames.has(name)) return;
  const r = await api().create_class(name);
  if (!r.ok) {
    const e = $('#modalError');
    e.textContent = r.error;         // e.g. "already exists" / "invalid name"
    e.className = 'modal-error';
    e.hidden = false;
    return;
  }
  closeModal();
  await refresh();
  toast(`הכיתה «${name}» נוצרה`);
}

async function confirmDeleteClass(name) {
  if (!window.confirm(`למחוק את כיתה ${name} ואת כל הקבצים שבה?`)) return;
  const r = await api().delete_class(name);
  if (r.ok) { await refresh(); toast(`כיתה ${name} נמחקה`); }
}

// ---- generate + progress drawer ----------------------------------------
async function generate() {
  if (running) return;
  const r = await api().generate();
  if (!r.ok) return toast(r.error || 'לא ניתן להפעיל', 'warn');
  running = true;
  openDrawer();
}

function openDrawer() {
  $('#log').innerHTML = '';
  $('#results').hidden = true;
  $('#results').innerHTML = '';
  $('#drawerFoot').hidden = true;
  $('#drawerClose').hidden = true;
  $('#drawerTitle').textContent = 'מפיק דוחות…';
  $('#progressFill').className = 'progress-fill indeterminate';
  $('#scrim').hidden = false;
  $('#drawer').hidden = false;
  $('#runStatus').textContent = 'מפיק דוחות…';
}
function closeDrawer() {
  $('#scrim').hidden = true;
  $('#drawer').hidden = true;
  $('#runStatus').textContent = '';
}

window.onProgress = function (ev) {
  if (ev.type === 'log') addLogLine(ev.line);
};

function addLogLine(line) {
  const div = el('div', 'line');
  const t = String(line).trim();
  if (/FAILED|שגיאה|Error/i.test(t)) div.classList.add('err');
  else if (/!|חסר|למזג|merge|missing/i.test(t)) div.classList.add('warn');
  else if (/->|wrote|✓/.test(t)) div.classList.add('ok');
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
  $('#drawerFoot').hidden = false;
  refresh();
};

function renderResults(res) {
  const box = $('#results');
  box.innerHTML = '';
  (res.classes || []).forEach(c => {
    const card = el('div', 'res-card');
    let html = `<h3>כיתה ${esc(c.class)} <span style="color:var(--ink-faint);font-weight:600">· ${c.students} תלמידים</span></h3>`;
    if (c.docx) html += `<div class="r-sub">${esc(c.docx)}</div>`;
    (Object.entries(c.merge || {})).forEach(([name, n]) => {
      html += `<div class="flag merge">🔗 ${n} אבחונים ל-${esc(name)} — שורות סמוכות, למזג ידנית</div>`;
    });
    if (c.review && c.review.length)
      html += `<div class="flag review">⚠ לבדוק ידנית: ${c.review.map(esc).join('، ')}</div>`;
    (c.failures || []).forEach(f => {
      html += `<div class="flag fail">✕ נכשל: ${esc(f)}</div>`;
    });
    card.innerHTML = html;
    box.appendChild(card);
  });
  box.hidden = false;
}

// ---- helpers ------------------------------------------------------------
function el(tag, cls) { const n = document.createElement(tag); if (cls) n.className = cls; return n; }
function esc(s) { return String(s == null ? '' : s).replace(/[&<>"]/g, m => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[m])); }
function cssEsc(s) { return String(s).replace(/["\\]/g, '\\$&'); }

let toastTimer;
function toast(msg, kind = '') {
  const t = $('#toast');
  t.textContent = msg;
  t.className = 'toast' + (kind ? ' ' + kind : '');
  t.hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => { t.hidden = true; }, 3200);
}
