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
const progressBlock = $("#progress-block");
const progressStage = $("#progress-stage");
const progressFill = $("#progress-fill");
const progressPct = $("#progress-pct");
const progressDetail = $("#progress-detail");

const STAGE_LABELS = {
  parsing: "Parseando transcripción",
  received: "Audio recibido",
  uploading: "Subiendo a AssemblyAI",
  uploaded: "Audio subido",
  assemblyai_queued: "En cola en AssemblyAI",
  assemblyai_processing: "AssemblyAI transcribiendo",
  assemblyai_completed: "Transcripción terminada",
  transcript_built: "Transcripción compuesta",
  saving: "Reservando registro",
  claude_start: "Llamando a Claude",
  claude_streaming: "Claude generando…",
  retrying: "Reintentando…",
  rendering: "Renderizando entregables",
  saving_final: "Guardando en base de datos",
  done: "PAQUETE generado",
  error: "Error",
};

let creator = null;
let checklistTemplate = [];
let uploadedFileContent = null;
let uploadedAudioFile = null;  // File object para el flujo audio
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
    bindAudioDropzone();
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

function bindAudioDropzone() {
  const audioZone = $("#audio-dropzone");
  const audioInput = $("#audio-input");
  const audioName = $("#audio-filename");
  if (!audioZone || !audioInput) return;

  audioZone.addEventListener("click", () => audioInput.click());
  audioInput.addEventListener("change", () => {
    if (audioInput.files[0]) acceptAudio(audioInput.files[0]);
  });
  ["dragenter", "dragover"].forEach(ev =>
    audioZone.addEventListener(ev, (e) => {
      e.preventDefault(); e.stopPropagation();
      audioZone.classList.add("drag-over");
    })
  );
  ["dragleave", "drop"].forEach(ev =>
    audioZone.addEventListener(ev, (e) => {
      e.preventDefault(); e.stopPropagation();
      audioZone.classList.remove("drag-over");
    })
  );
  audioZone.addEventListener("drop", (e) => {
    if (e.dataTransfer.files[0]) acceptAudio(e.dataTransfer.files[0]);
  });

  function acceptAudio(file) {
    const MAX_MB = 100;
    const sizeMB = file.size / 1024 / 1024;
    if (sizeMB > MAX_MB) {
      audioName.textContent = `⚠️ ${file.name} pesa ${sizeMB.toFixed(1)} MB. Máximo ${MAX_MB} MB. Exporta a MP3 a 128 kbps.`;
      audioName.style.color = "var(--error)";
      uploadedAudioFile = null;
      return;
    }
    audioName.style.color = "";
    audioName.textContent = `Audio cargado: ${file.name} (${sizeMB.toFixed(1)} MB · ${file.type || "tipo desconocido"})`;
    uploadedAudioFile = file;
    if (!titleInput.value.trim()) titleInput.value = file.name.replace(/\.[^.]+$/, "");
  }
}

function bindProcess() {
  processBtn.addEventListener("click", async () => {
    let fetchPromise;

    if (activeTab === "audio") {
      if (!uploadedAudioFile) {
        statusLine.textContent = "Arrastra un audio MP3/M4A/WAV en la zona azul.";
        statusLine.classList.add("error");
        return;
      }
      statusLine.classList.remove("error", "success");
      statusLine.textContent = "";
      processBtn.disabled = true;
      showProgress("Subiendo a Vercel Blob…", 0, "");

      let blobUrl;
      try {
        blobUrl = await uploadToVercelBlob(uploadedAudioFile, (pct) => {
          // 0 → 6% del progreso total mientras sube a Blob
          showProgress("Subiendo a Vercel Blob", pct * 0.06, `${(pct).toFixed(0)}% del archivo`);
        });
      } catch (err) {
        statusLine.textContent = "Error subiendo a Vercel Blob: " + err.message;
        statusLine.classList.add("error");
        showProgress("Error", 100, err.message, "error");
        processBtn.disabled = false;
        return;
      }

      fetchPromise = fetch(`/api/creators/${slug}/process-audio-url`, {
        method: "POST",
        headers: { "Content-Type": "application/json", "Accept": "text/event-stream" },
        body: JSON.stringify({
          blob_url: blobUrl,
          original_filename: uploadedAudioFile.name,
          title_hint: titleInput.value.trim(),
          type_hint: typeSelect.value || "",
        }),
      });
    } else {
      const transcript = (activeTab === "paste"
                          ? textarea.value
                          : uploadedFileContent || "").trim();
      if (!transcript) {
        statusLine.textContent = "Necesitas pegar, subir transcript o cargar un audio.";
        statusLine.classList.add("error");
        return;
      }
      fetchPromise = fetch(`/api/creators/${slug}/process`, {
        method: "POST",
        headers: { "Content-Type": "application/json", "Accept": "text/event-stream" },
        body: JSON.stringify({
          transcript,
          title_hint: titleInput.value.trim(),
          type_hint: typeSelect.value || "",
        }),
      });
    }

    statusLine.classList.remove("error", "success");
    statusLine.textContent = "";
    processBtn.disabled = true;
    showProgress("Iniciando…", 0, "");

    try {
      const res = await fetchPromise;
      if (!res.ok || !res.body) {
        const txt = await res.text();
        throw new Error(`${res.status}: ${txt}`);
      }

      const reader = res.body.getReader();
      const decoder = new TextDecoder();
      let buffer = "";
      let finalVideo = null;
      let errorMsg = null;

      while (true) {
        const { value, done } = await reader.read();
        if (done) break;
        buffer += decoder.decode(value, { stream: true });
        let idx;
        while ((idx = buffer.indexOf("\n\n")) !== -1) {
          const block = buffer.slice(0, idx);
          buffer = buffer.slice(idx + 2);
          if (!block.startsWith("data:")) continue;
          const payload = block.replace(/^data:\s*/, "");
          let evt;
          try { evt = JSON.parse(payload); }
          catch { continue; }

          const label = STAGE_LABELS[evt.stage] || evt.stage;
          const detail = evt.message || "";
          if (evt.stage === "done") {
            finalVideo = evt.video;
            showProgress(label, 100, detail, "done");
          } else if (evt.stage === "error") {
            errorMsg = evt.error || "Error desconocido";
            showProgress("Error", 100, errorMsg, "error");
          } else {
            showProgress(label, evt.progress || 0, detail);
          }
        }
      }

      if (errorMsg) throw new Error(errorMsg);
      if (!finalVideo) throw new Error("Stream cerrado sin evento `done`");

      statusLine.textContent = `✓ PAQUETE generado: ${finalVideo.code} · ${finalVideo.title}`;
      statusLine.classList.add("success");
      textarea.value = "";
      titleInput.value = "";
      uploadedFileContent = null;
      uploadedAudioFile = null;
      filenameDisplay.textContent = "";
      const audioName = $("#audio-filename");
      if (audioName) audioName.textContent = "";
      await refreshVideos(finalVideo.id);
      setTimeout(() => progressBlock.classList.add("hidden"), 2500);

    } catch (err) {
      statusLine.textContent = "Error: " + err.message;
      statusLine.classList.add("error");
      showProgress("Error", 100, err.message, "error");
    } finally {
      processBtn.disabled = false;
    }
  });
}

function showProgress(stage, pct, detail, state) {
  progressBlock.classList.remove("hidden", "done", "error");
  if (state) progressBlock.classList.add(state);
  progressStage.textContent = stage;
  const p = Math.max(0, Math.min(100, Number(pct) || 0));
  progressFill.style.width = `${p}%`;
  progressPct.textContent = `${p.toFixed(1)}%`;
  progressDetail.textContent = detail || "";
}

// ──────────────────────────────────────────────────────────────────
// Vercel Blob upload: el navegador sube DIRECTO a Blob storage para
// saltarse el límite de 4.5 MB de las funciones. Pedimos un token a
// /api/blob/handle-upload y luego hacemos PUT con XHR (para progreso).
// ──────────────────────────────────────────────────────────────────
async function uploadToVercelBlob(file, onProgress) {
  // Sanea el nombre: solo letras/números/punto/guión y limita longitud
  const safeName = file.name
    .replace(/[^\w.\-]+/g, "_")
    .replace(/_+/g, "_")
    .slice(0, 80);
  const pathname = `audio/${safeName}`;

  // 1) Pedir clientToken al backend
  const tokenRes = await fetch("/api/blob/handle-upload", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      type: "blob.generate-client-token",
      payload: {
        pathname,
        callbackUrl: null,
        clientPayload: null,
        multipart: false,
      },
    }),
  });
  if (!tokenRes.ok) {
    const t = await tokenRes.text();
    throw new Error(`token ${tokenRes.status}: ${t.slice(0, 200)}`);
  }
  const tokenData = await tokenRes.json();
  const clientToken = tokenData.clientToken;
  if (!clientToken) throw new Error("Backend no devolvió clientToken");

  // El SDK envía PUT a https://vercel.com/api/blob/?pathname=... (no a
  // blob.vercel-storage.com, esa URL es solo para LEER blobs públicos).
  // Headers verificados contra @vercel/blob v1.x.
  const storeIdMatch = clientToken.match(/^vercel_blob_client_([^_]+)_/);
  const storeId = storeIdMatch ? storeIdMatch[1] : "";

  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest();
    const params = new URLSearchParams({ pathname });
    xhr.open("PUT", `https://vercel.com/api/blob/?${params.toString()}`);
    xhr.setRequestHeader("authorization", `Bearer ${clientToken}`);
    xhr.setRequestHeader("x-api-version", "12");
    xhr.setRequestHeader("x-vercel-blob-store-id", storeId);
    xhr.setRequestHeader("x-vercel-blob-access", "public");
    xhr.setRequestHeader("x-content-type", file.type || "application/octet-stream");
    xhr.setRequestHeader("x-content-length", String(file.size));

    xhr.upload.addEventListener("progress", (e) => {
      if (e.lengthComputable && onProgress) {
        onProgress((e.loaded / e.total) * 100);
      }
    });
    xhr.onload = () => {
      if (xhr.status >= 200 && xhr.status < 300) {
        try {
          const data = JSON.parse(xhr.responseText);
          if (!data.url) throw new Error("Blob sin campo url");
          resolve(data.url);
        } catch (e) {
          reject(new Error("Respuesta de Blob inválida: " + e.message));
        }
      } else {
        reject(new Error(`Blob ${xhr.status}: ${xhr.responseText.slice(0, 250)}`));
      }
    };
    xhr.onerror = () => reject(new Error("Error de red subiendo a Blob"));
    xhr.send(file);
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

const fullVideoCache = new Map();

async function fetchFullVideo(videoId) {
  if (fullVideoCache.has(videoId)) return fullVideoCache.get(videoId);
  const res = await fetch(`/api/videos/${videoId}`);
  if (!res.ok) throw new Error(`Failed to fetch video ${videoId}: ${res.status}`);
  const data = await res.json();
  fullVideoCache.set(videoId, data);
  return data;
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
    <div class="video-id" data-role="edit-code" title="Click para editar código">${escapeHtml(v.code)}</div>
    <div class="video-title-wrap">
      <span class="video-title" data-role="title-text">${escapeHtml(v.title)}</span>
      <button class="title-edit-btn" data-role="edit-title" title="Editar título">✎</button>
    </div>
    <div class="video-meta">
      <span class="badge ${v.type}">${escapeHtml(v.type)}</span>
      <span>${escapeHtml(v.duration)}</span>
      <span class="progress-circle">${done}/${total} · ${pct}%</span>
    </div>
    <div class="expand-icon">▾</div>
  `;

  // Editar título (lápiz)
  header.querySelector('[data-role="edit-title"]').addEventListener("click", (e) => {
    e.stopPropagation();
    startInlineEdit(header, v, "title");
  });
  // Editar código (click en el V##)
  header.querySelector('[data-role="edit-code"]').addEventListener("click", (e) => {
    e.stopPropagation();
    startInlineEdit(header, v, "code");
  });

  const body = document.createElement("div");
  body.className = "video-body";

  const grid = document.createElement("div");
  grid.className = "body-grid";
  const paqueteBlock = document.createElement("div");
  paqueteBlock.className = "paquete-block";
  paqueteBlock.innerHTML = `<h4>PAQUETE</h4><div class="paquete-loading">Cargando…</div>`;
  grid.appendChild(paqueteBlock);
  grid.appendChild(buildChecklistBlock(v));
  body.appendChild(grid);

  let loaded = false;
  async function ensureLoaded() {
    if (loaded) return;
    loaded = true;
    try {
      const full = await fetchFullVideo(v.id);
      const paqueteContent = renderPaquete(full);
      paqueteBlock.innerHTML = "";
      const h4 = document.createElement("h4");
      h4.textContent = "PAQUETE";
      paqueteBlock.appendChild(h4);
      paqueteBlock.appendChild(paqueteContent);
    } catch (err) {
      paqueteBlock.innerHTML = `<h4>PAQUETE</h4><p class="error-text">Error cargando: ${escapeHtml(err.message)}</p>`;
    }
  }
  if (expanded) ensureLoaded();
  header.addEventListener("click", () => {
    card.classList.toggle("expanded");
    if (card.classList.contains("expanded")) ensureLoaded();
  });

  card.appendChild(header);
  card.appendChild(body);
  return card;
}

function renderPaquete(v) {
  const wrap = document.createElement("div");
  const p = v.paquete || {};

  if (v.error_message) {
    const err = document.createElement("p");
    err.className = "error-text";
    err.textContent = "Error: " + v.error_message;
    wrap.appendChild(err);
  }

  if (v.suggested_publish_at) {
    const hint = document.createElement("p");
    hint.className = "publish-hint";
    hint.textContent = `Publicación sugerida: ${formatDate(v.suggested_publish_at)}`;
    wrap.appendChild(hint);
  }

  // Título principal + alternativas
  if (p.title) {
    wrap.appendChild(copyBlock({
      label: "Título principal",
      meta: `${p.title.length} caracteres`,
      content: p.title,
    }));
  }
  if (Array.isArray(p.alternatives) && p.alternatives.length) {
    wrap.appendChild(copyBlock({
      label: "Alternativas A/B",
      content: p.alternatives.map((a, i) => `${i + 1}. ${a}`).join("\n"),
    }));
  }

  if (p.description) {
    wrap.appendChild(copyBlock({
      label: "Descripción YouTube",
      content: p.description,
      multiline: true,
    }));
  }

  if (p.chapters) {
    wrap.appendChild(copyBlock({
      label: "Capítulos",
      content: p.chapters,
      multiline: true,
    }));
  }

  if (p.tags) {
    wrap.appendChild(copyBlock({
      label: "Tags",
      content: p.tags,
    }));
  }

  if (p.pinned_comment) {
    wrap.appendChild(copyBlock({
      label: "Comentario fijado",
      content: p.pinned_comment,
      multiline: true,
    }));
  }

  // Miniatura
  const thumbWrap = document.createElement("div");
  thumbWrap.className = "thumb-section";
  thumbWrap.innerHTML = `<h5 class="section-title">Miniatura · Plantilla ${escapeHtml(String(p.thumb_template ?? ""))}</h5>`;
  if (p.thumb_textA) {
    thumbWrap.appendChild(copyBlock({ label: "Texto A", content: p.thumb_textA, compact: true }));
  }
  if (p.thumb_textB) {
    thumbWrap.appendChild(copyBlock({ label: "Texto B", content: p.thumb_textB, compact: true }));
  }
  if (p.thumb_prompt) {
    thumbWrap.appendChild(copyBlock({
      label: "Prompt image-gen (inglés)",
      content: p.thumb_prompt,
      multiline: true,
    }));
  }
  if (p.thumb_textA || p.thumb_textB || p.thumb_prompt) wrap.appendChild(thumbWrap);

  // Midform
  if (Array.isArray(p.midform) && p.midform.length) {
    const mfWrap = document.createElement("div");
    mfWrap.className = "clips-section";
    mfWrap.innerHTML = `<h5 class="section-title">Midform (${p.midform.length} piezas)</h5>`;
    p.midform.forEach((m, i) => mfWrap.appendChild(renderClip(m, "Midform", i + 1)));
    wrap.appendChild(mfWrap);
  }

  // Shorts
  if (Array.isArray(p.shorts) && p.shorts.length) {
    const sWrap = document.createElement("div");
    sWrap.className = "clips-section";
    sWrap.innerHTML = `<h5 class="section-title">Shorts (${p.shorts.length} piezas)</h5>`;
    p.shorts.forEach((s, i) => sWrap.appendChild(renderClip(s, "Short", i + 1, true)));
    wrap.appendChild(sWrap);
  }

  // Alertas
  if (Array.isArray(p.alerts) && p.alerts.length) {
    const aWrap = document.createElement("div");
    aWrap.className = "alerts-section";
    aWrap.innerHTML = `<h5 class="section-title">Alertas de monetización</h5>`;
    const ul = document.createElement("ul");
    ul.className = "alerts-list";
    p.alerts.forEach(a => {
      const li = document.createElement("li");
      li.innerHTML = `<strong>${escapeHtml(a.timestamp || "")}</strong> · ${escapeHtml(a.section || "")} — <em>${escapeHtml(a.risk || "")}</em><br/><span class="alert-fix">${escapeHtml(a.adjustment || "")}</span>`;
      ul.appendChild(li);
    });
    aWrap.appendChild(ul);
    wrap.appendChild(aWrap);
  }

  // Archivos descargables (collapsed by default)
  const filesDetails = document.createElement("details");
  filesDetails.className = "files-details";
  filesDetails.innerHTML = `<summary>Archivos descargables (PAQUETE.md, CSV, JSON…)</summary>`;
  const ul = document.createElement("ul");
  ul.className = "files-list";
  const fileMap = [
    ["PAQUETE.md", "PAQUETE.md completo"],
    ["transcripcion.txt", "Transcripción"],
    ["descripcion.txt", "Descripción YouTube (.txt)"],
    ["cortes_editor.csv", "Cortes editor (CSV)"],
    ["miniatura.txt", "Miniatura (prompt + textos)"],
    ["paquete.json", "paquete.json (raw)"],
  ];
  for (const [fname, label] of fileMap) {
    const li = document.createElement("li");
    li.innerHTML = `<span>${escapeHtml(label)}</span><a href="/api/videos/${v.id}/files/${encodeURIComponent(fname)}" target="_blank" rel="noopener">Descargar</a>`;
    ul.appendChild(li);
  }
  filesDetails.appendChild(ul);
  wrap.appendChild(filesDetails);

  return wrap;
}

function renderClip(clip, kind, num, isShort = false) {
  const wrap = document.createElement("div");
  wrap.className = "clip-card";
  const header = document.createElement("div");
  header.className = "clip-header";
  const inVerified = clip._in_verified === true;
  const outVerified = clip._out_verified === true;
  let verifyBadge = "";
  if (inVerified && outVerified) {
    verifyBadge = `<span class="ts-badge ok" title="Timestamps verificados contra la transcripción">✓ verificado</span>`;
  } else if (inVerified || outVerified) {
    verifyBadge = `<span class="ts-badge partial" title="Solo uno de los dos timestamps verificado">⚠ parcial</span>`;
  } else {
    verifyBadge = `<span class="ts-badge bad" title="No se encontró la frase citada en la transcripción — el timestamp puede no ser correcto">⚠ sin verificar</span>`;
  }
  header.innerHTML = `
    <strong>${kind} ${num}</strong>
    <span class="clip-title">${escapeHtml(clip.title || "")}</span>
    <span class="clip-times">${escapeHtml(clip.in || "")} → ${escapeHtml(clip.out || "")} · ${escapeHtml(clip.duration || "")}</span>
    ${verifyBadge}
  `;
  wrap.appendChild(header);

  const brief = [
    `${kind} ${num} — ${clip.title || ""}`,
    `IN: ${clip.in || ""}  OUT: ${clip.out || ""}  DUR: ${clip.duration || ""}`,
    `Frase entrada: "${clip.phrase_in || ""}"`,
  ];
  if (clip.phrase_out) brief.push(`Frase salida: "${clip.phrase_out}"`);
  if (clip.burn_text) brief.push(`Texto a quemar: ${clip.burn_text}`);
  if (clip.why_works) brief.push(`Por qué funciona: ${clip.why_works}`);

  wrap.appendChild(copyBlock({
    label: "Brief para editor",
    content: brief.join("\n"),
    multiline: true,
    compact: true,
  }));
  if (clip.burn_text) {
    wrap.appendChild(copyBlock({
      label: "Texto miniatura",
      content: clip.burn_text,
      compact: true,
    }));
  }
  if (clip.thumb_prompt) {
    wrap.appendChild(copyBlock({
      label: "Prompt imagen (image-gen, EN)",
      content: clip.thumb_prompt,
      multiline: true,
      compact: true,
    }));
  }
  return wrap;
}

function copyBlock({ label, content, meta, multiline = false, compact = false }) {
  const block = document.createElement("div");
  block.className = "copy-block" + (compact ? " compact" : "") + (multiline ? " multiline" : "");

  const head = document.createElement("div");
  head.className = "copy-head";
  head.innerHTML = `
    <span class="copy-label">${escapeHtml(label)}</span>
    ${meta ? `<span class="copy-meta">${escapeHtml(meta)}</span>` : ""}
    <button class="copy-btn" type="button">Copiar</button>
  `;
  const pre = document.createElement(multiline ? "pre" : "div");
  pre.className = "copy-content";
  pre.textContent = content || "";

  head.querySelector(".copy-btn").addEventListener("click", async (e) => {
    const btn = e.currentTarget;
    try {
      await navigator.clipboard.writeText(content || "");
      const orig = btn.textContent;
      btn.textContent = "✓ Copiado";
      btn.classList.add("copied");
      setTimeout(() => {
        btn.textContent = orig;
        btn.classList.remove("copied");
      }, 1500);
    } catch (err) {
      btn.textContent = "Error";
    }
  });

  block.appendChild(head);
  block.appendChild(pre);
  return block;
}

function buildChecklistBlock(v) {
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
    fullVideoCache.delete(v.id);
    await refreshVideos(v.id);
  });
  actions.appendChild(clearBtn);

  const deleteBtn = document.createElement("button");
  deleteBtn.className = "btn-danger";
  deleteBtn.textContent = "🗑 Eliminar vídeo";
  deleteBtn.addEventListener("click", async (e) => {
    e.stopPropagation();
    const ok = confirm(
      `¿Eliminar definitivamente ${v.code} (${v.title})?\n\n` +
      "Se borra el vídeo, su PAQUETE y su checklist. No se puede deshacer."
    );
    if (!ok) return;
    const res = await fetch(`/api/videos/${v.id}`, { method: "DELETE" });
    if (!res.ok) {
      alert("Error al eliminar: " + (await res.text()));
      return;
    }
    fullVideoCache.delete(v.id);
    await refreshVideos();
  });
  actions.appendChild(deleteBtn);

  checklistBlock.appendChild(actions);
  return checklistBlock;
}

async function startInlineEdit(headerEl, video, field) {
  // field: "title" | "code"
  const targetEl = headerEl.querySelector(
    field === "title" ? '[data-role="title-text"]' : '[data-role="edit-code"]'
  );
  if (!targetEl) return;
  const original = field === "title" ? video.title : video.code;

  // Crea input que reemplaza el span/div
  const input = document.createElement("input");
  input.type = "text";
  input.value = original;
  input.className = field === "title" ? "title-edit-input" : "code-edit-input";
  input.maxLength = field === "title" ? 200 : 20;

  const placeholder = document.createElement("span");
  targetEl.replaceWith(placeholder);
  placeholder.replaceWith(input);
  input.focus();
  input.select();

  let saved = false;
  async function commit() {
    if (saved) return; saved = true;
    const newValue = input.value.trim();
    if (!newValue || newValue === original) {
      revert(); return;
    }
    input.disabled = true;
    try {
      const body = field === "title" ? { title: newValue } : { code: newValue };
      const res = await fetch(`/api/videos/${video.id}`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      });
      if (!res.ok) {
        const err = await res.text();
        alert("No se pudo guardar: " + err);
        revert(); return;
      }
      // Actualiza UI sin re-render completo
      fullVideoCache.delete(video.id);
      if (field === "title") video.title = newValue;
      else video.code = newValue;
      const restored = document.createElement(field === "title" ? "span" : "div");
      restored.className = field === "title" ? "video-title" : "video-id";
      restored.dataset.role = field === "title" ? "title-text" : "edit-code";
      if (field === "code") restored.title = "Click para editar código";
      restored.textContent = newValue;
      input.replaceWith(restored);
      restored.addEventListener("click", (e) => {
        e.stopPropagation();
        startInlineEdit(headerEl, video, field);
      });
    } catch (err) {
      alert("Error: " + err.message);
      revert();
    }
  }
  function revert() {
    input.replaceWith(targetEl);
  }

  input.addEventListener("keydown", (e) => {
    if (e.key === "Enter") { e.preventDefault(); commit(); }
    else if (e.key === "Escape") { e.preventDefault(); saved = true; revert(); }
  });
  input.addEventListener("blur", commit);
  input.addEventListener("click", (e) => e.stopPropagation());
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
