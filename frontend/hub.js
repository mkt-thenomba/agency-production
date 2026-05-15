// Hub: lista los creators y los abre en su sub-dashboard.

const grid = document.getElementById("creators-grid");

async function load() {
  try {
    const res = await fetch("/api/creators");
    const data = await res.json();
    render(data.creators || []);
  } catch (err) {
    grid.innerHTML = `<p style="color:#fff">Error cargando creators: ${err.message}</p>`;
  }
}

function render(creators) {
  grid.innerHTML = "";
  for (const c of creators) {
    const card = document.createElement("a");
    card.href = `/c/${c.slug}`;
    card.className = "creator-card" + (c.is_placeholder ? " placeholder" : "");
    card.style.borderLeftColor = c.color_primary;

    card.innerHTML = `
      <div class="creator-card-top">
        <div class="creator-avatar" style="background:${c.color_primary}; color:${c.color_secondary}">
          ${escapeHtml(c.avatar_initials || c.name.slice(0,2).toUpperCase())}
        </div>
        <div>
          <div class="creator-name">${escapeHtml(c.name)}</div>
          <div class="creator-sub">${escapeHtml(c.subtitle || "")}</div>
        </div>
      </div>
      <div class="creator-meta">
        <span>${c.video_count} vídeo${c.video_count === 1 ? "" : "s"}</span>
        <span>→</span>
      </div>
    `;
    grid.appendChild(card);
  }
}

function escapeHtml(s) {
  return String(s ?? "")
    .replace(/&/g, "&amp;").replace(/</g, "&lt;")
    .replace(/>/g, "&gt;").replace(/"/g, "&quot;");
}

load();
