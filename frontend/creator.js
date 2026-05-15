// Sub-dashboard: pegar transcript / subir JSON → POST /process → lista vídeos + checklist.

const slug = location.pathname.split("/").filter(Boolean)[1];

const $ = (s) => document.querySelector(s);
const headerEl = $("#creator-header");
const warningEl = $("#placeholder-warning");
const titleInput = $("#title-hint");
const typeSelect = $("#type-hint");
const textarea = $("#transcript-text");
const fileInput = $("#file-input");
const dropzone = $("#dropzone");
const filenameDisplay = $("#filename-display");
const processBtn = $("#process-btn");
const statusLine = $("#status-line");
const videosList = $("#videos-list");
const emptyState = $("#empty-state");
const refreshBtn = $("#refresh-btn");

let creator = null;
let checklistTemplate = [];
let uploadedFileContent = null;
let activeTab = "paste";

init();

async function init() {
  try {
    const res = await fetch(`/api/creators/${slug}`);
    if (!res.ok) {
      headerEl.innerHTML = `<h1>Creator no encontrado</h1>`;
      return;
    }
    creator = await res.json();
    checklistTemplate = creator.checklist_template || [];
    renderHeader();
    bindTabs();
    bindDropzone();
    bindProcess();
    bindRefresh();
    await refreshVideos();
  } catch (err) {
    statusLine.textContent = "Error inicializando: " + err.message;
    statusLine.classList.add("error");
  }
}

function renderHeader() {
  headerEl.innerHTML = `
    <div class="avatar" style="background:${creator.color_primary}; color:${creator.color_secondary}">
      ${escapeHtml(creator.avatar_initials || creator.name.slice(0,2).toUpperCase())}
    </div>
    <div>
      <h1>${escapeHtml(creator.name)}</h1>
      <div class="sub">${escapeHtml(creator.subtitle || "")}</div>
    </div>
  `;
  if (creator.is_placeholder) {
    warningEl.classList.remove("hidden");
    warningEl.innerHTML = `
      <strong>Creator todavía sin configuración específica.</strong>
      Puedes procesar transcripciones, pero Claude usa un prompt genérico hasta que Pablo pase
      las indicaciones del canal (voz, libros, plantillas).
    `;
  }
}

function bindTabs() {
  document.querySelectorAll(".tab-btn").forEach(btn => {
    btn.addEventListener("click", () => {
      document.querySelectorAll(".tab-btn").forEach(b => b.classList.remove("active"));
      btn.classList.add("active");
      activeTab = btn.dataset.tab;
      document.querySelectorAll(".tab-panel").forEach(p => {
        p.classList.toggle("hidden", p.dataset.tabPanel !== activeTab);
      });
    });
  });
}

function bindDropzone() {
  dropzone.addEventListener("click", () => fileInput.click());
  fileInput.addEventListener("change", () => {
    if (fileInput.files[0]) handleFile(fileInput.files[0]);
  });
  ["dragenter", "dragover"].forEach(ev =>
    dropzone.addEventListener(ev, (e) => {
      e.preventDefault(); e.stopPropagation();
      dropzone.classList.add("drag-over");
    })
  );
  ["dragleave", "drop"].forEach(ev =>
    dropzone.addEventListener(ev, (e) => {
      e.preventDefault(); e.stopPropagation();
      dropzone.classList.remove("drag-over");
    })
  );
  dropzone.addEventListener("drop", (e) => {
    if (e.dataTransfer.files[0]) handleFile(e.dataTransfer.files[0]);
  });
}

async function handleFile(file) {
  filenameDisplay.textContent = `Archivo cargado: ${file.name} (${(file.size/1024).toFixed(1)} KB)`;
  uploadedFileContent = await file.text();
  if (!titleInput.value.trim()) titleInput.value = file.name.replace(/\.[^.]+$/, "");
}

function bindProcess() {
  processBtn.addEventListener("click", async () => {
    let transcript = "";
    if (activeTab === "paste") transcript = textarea.value.trim();
    else transcript = (uploadedFileContent || "").trim();

    if (!transcript) {
      statusLine.textContent = "Necesitas pegar o subir una transcripción.";
      statusLine.classList.add("error");
      return;
    }

    statusLine.classList.remove("error", "success");
    statusLine.textContent = "Generando PAQUETE con Claude… (~15-30 s)";
    processBtn.disabled = true;

    try {
      const res = await fetch(`/api/creators/${slug}/process`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          transcript,
          title_hint: titleInput.value.trim(),
          type_hint: typeSelect.value || "",
        }),
      });
      if (!res.ok) {
        const txt = await res.text();
        throw new Error(`${res.status}: ${txt}`);
      }
      const video = await res.json();
      statusLine.textContent = `✓ PAQUETE generado: ${video.code} · ${video.title}`;
      statusLine.classList.add("success");
      textarea.value = "";
      titleInput.value = "";
      uploadedFileContent = null;
      filenameDisplay.textContent = "";
      await refreshVideos(video.id);
    } catch (err) {
      statusLine.textContent = "Error: " + err.message;
      statusLine.classList.add("error");
    } finally {
      processBtn.disabled = false;
    }
  });
}

function bindRefresh() {
  refreshBtn.addEventListener("click", () => refreshVideos());
}

async function refreshVideos(expandId) {
  try {
    const res = await fetch(`/api/creators/${slug}/videos`);
    const data = await res.json();
    renderVideos(data.videos || [], expandId);
  } catch (err) {
    console.error(err);
  }
}

function renderVideos(videos, expandId) {
  videosList.innerHTML = "";
  if (videos.length === 0) {
    emptyState.classList.remove("hidden");
    return;
  }
  emptyState.classList.add("hidden");
  for (const v of videos) {
    videosList.appendChild(renderCard(v, expandId === v.id));
  }
}

function renderCard(v, expanded) {
  const card = document.createElement("article");
  card.className = `video-card status-${v.status}` + (expanded ? " expanded" : "");
  card.dataset.id = v.id;

  const total = checklistTemplate.length || 1;
  const done = Object.values(v.checklist).filter(Boolean).length;
  const pct = Math.round((done / total) * 100);

  const header = document.createElement("div");
  header.className = "video-header";
  header.innerHTML = `
    <div class="video-id">${escapeHtml(v.code)}</div>
    <div class="video-title">${escapeHtml(v.title)}</div>
    <div class="video-meta">
      <span class="badge ${v.type}">${escapeHtml(v.type)}</span>
      <span>${escapeHtml(v.duration)}</span>
      <span class="progress-circle">${done}/${total} · ${pct}%</span>
    </div>
    <div class="expand-icon">▾</div>
  `;
  header.addEventListener("click", () => card.classList.toggle("expanded"));

  const body = document.createElement("div");
  body.className = "video-body";
  body.appendChild(renderBody(v));

  card.appendChild(header);
  card.appendChild(body);
  return card;
}

function renderBody(v) {
  const wrap = document.createElement("div");
  wrap.className = "body-grid";

  // Files block
  const filesBlock = document.createElement("div");
  filesBlock.className = "files-block";
  filesBlock.innerHTML = `<h4>Entregables</h4>`;
  const ul = document.createElement("ul");
  ul.className = "files-list";
  const fileMap = [
    ["PAQUETE.md", "PAQUETE.md"],
    ["transcripcion.txt", "Transcripción"],
    ["descripcion.txt", "Descripción YouTube"],
    ["cortes_editor.csv", "Cortes editor (CSV)"],
    ["miniatura.txt", "Miniatura (prompt + textos)"],
    ["paquete.json", "paquete.json (raw)"],
  ];
  for (const [fname, label] of fileMap) {
    const li = document.createElement("li");
    li.innerHTML = `
      <span>${escapeHtml(label)}</span>
      <a href="/api/videos/${v.id}/files/${encodeURIComponent(fname)}" target="_blank" rel="noopener">Descargar</a>
    `;
    ul.appendChild(li);
  }
  filesBlock.appendChild(ul);
  if (v.suggested_publish_at) {
    const hint = document.createElement("p");
    hint.className = "publish-hint";
    hint.textContent = `Publicación sugerida: ${formatDate(v.suggested_publish_at)}`;
    filesBlock.appendChild(hint);
  }
  if (v.error_message) {
    const err = document.createElement("p");
    err.className = "publish-hint";
    err.style.color = "var(--error)";
    err.textContent = "Error: " + v.error_message;
    filesBlock.appendChild(err);
  }

  // Checklist block
  const checklistBlock = document.createElement("div");
  checklistBlock.className = "checklist-block";
  checklistBlock.innerHTML = `<h4>Checklist</h4>`;
  const phases = {};
  for (const item of checklistTemplate) {
    (phases[item.phase] ||= []).push(item);
  }
  for (const [phase, items] of Object.entries(phases)) {
    const block = document.createElement("div");
    block.className = "checklist-phase";
    block.innerHTML = `<h5>${escapeHtml(phase)}</h5>`;
    for (const item of items) {
      const row = document.createElement("div");
      row.className = "checklist-item" + (v.checklist[item.key] ? " done" : "");
      const id = `chk_${v.id}_${item.key}`;
      row.innerHTML = `
        <input type="checkbox" id="${id}" ${v.checklist[item.key] ? "checked" : ""} />
        <label for="${id}">${escapeHtml(item.label)}</label>
      `;
      const input = row.querySelector("input");
      input.addEventListener("change", async () => {
        await fetch(`/api/videos/${v.id}/checklist`, {
          method: "PATCH",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ item_key: item.key, done: input.checked }),
        });
        row.classList.toggle("done", input.checked);
        updateProgress(v.id);
      });
      block.appendChild(row);
    }
    checklistBlock.appendChild(block);
  }

  const actions = document.createElement("div");
  actions.className = "checklist-actions";
  const clearBtn = document.createElement("button");
  clearBtn.className = "btn-ghost";
  clearBtn.textContent = "Limpiar checklist";
  clearBtn.addEventListener("click", async (e) => {
    e.stopPropagation();
    if (!confirm("¿Reiniciar checklist?")) return;
    await fetch(`/api/videos/${v.id}/checklist`, { method: "DELETE" });
    await refreshVideos(v.id);
  });
  actions.appendChild(clearBtn);
  checklistBlock.appendChild(actions);

  wrap.appendChild(filesBlock);
  wrap.appendChild(checklistBlock);
  return wrap;
}

function updateProgress(videoId) {
  const card = videosList.querySelector(`.video-card[data-id="${videoId}"]`);
  if (!card) return;
  const total = card.querySelectorAll(".checklist-item input").length;
  const done = card.querySelectorAll(".checklist-item input:checked").length;
  const pct = total > 0 ? Math.round((done / total) * 100) : 0;
  const span = card.querySelector(".progress-circle");
  if (span) span.textContent = `${done}/${total} · ${pct}%`;
}

function formatDate(iso) {
  try {
    return new Date(iso).toLocaleString("es-ES", {
      weekday: "long", day: "2-digit", month: "long", hour: "2-digit", minute: "2-digit",
    });
  } catch { return iso; }
}

function escapeHtml(s) {
  return String(s ?? "")
    .replace(/&/g, "&amp;").replace(/</g, "&lt;")
    .replace(/>/g, "&gt;").replace(/"/g, "&quot;");
}
