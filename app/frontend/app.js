// Middle-earth MTG Management — frontend logic.
// API base is resolved relative to the current document so it works both on the
// direct port (http://host:8094/) and behind the Caddy path prefix (/mtg/).
const API = new URL("api/", window.location.href).href;

const api = {
  async get(path) {
    const r = await fetch(API + path);
    if (!r.ok) throw new Error(await r.text());
    return r.json();
  },
  async send(method, path, body) {
    const r = await fetch(API + path, {
      method,
      headers: { "Content-Type": "application/json" },
      body: body ? JSON.stringify(body) : undefined,
    });
    if (!r.ok) throw new Error(await r.text());
    return r.status === 204 ? null : r.json();
  },
};

// ---------- Navigation ----------
const views = document.querySelectorAll(".view");
const navButtons = document.querySelectorAll("#nav button");
navButtons.forEach((b) =>
  b.addEventListener("click", () => switchView(b.dataset.view))
);

function switchView(name) {
  navButtons.forEach((b) => b.classList.toggle("active", b.dataset.view === name));
  views.forEach((v) => v.classList.toggle("active", v.id === "view-" + name));
  const loaders = { dashboard: loadDashboard, collection: loadCollection, sets: loadSets, wishlist: loadWishlist, deck: loadDeck };
  (loaders[name] || (() => {}))();
}

// ---------- Dashboard ----------
async function loadDashboard() {
  const s = await api.get("collection/summary");
  document.getElementById("dash-cards").innerHTML = [
    stat("Catalogued owned", `${s.unique_owned}/${s.unique_total}`, `${s.completion}% del catalogo`, s.completion),
    stat("Total copies", s.total_copies, "physical cards"),
    stat("Missing (catalogue)", s.unique_missing, "catalogued cards not owned"),
    stat("Key Aragorn gaps", s.missing_key_aragorn, "missing Rare/Mythic (synergy ≥3)"),
    stat("Aragorn deck", `${s.deck_slots_filled}/${s.deck_size_target}`, `${s.deck_to_buy} still to buy`),
    stat("Wishlist value", `£${s.wishlist_value}`, "at target price"),
  ].join("");
  document.getElementById("dash-rarity").innerHTML = bars(s.missing_by_rarity);
  document.getElementById("dash-colour").innerHTML = bars(s.missing_by_colour);
}

function stat(label, value, sub, pct) {
  const bar = pct != null ? `<div class="progress"><span style="width:${pct}%"></span></div>` : "";
  return `<div class="stat"><div class="label">${label}</div><div class="value">${value}</div><div class="sub">${sub || ""}</div>${bar}</div>`;
}

function bars(obj) {
  const entries = Object.entries(obj).filter(([k]) => k);
  if (!entries.length) return '<p class="hint">Nothing missing 🎉</p>';
  const max = Math.max(...entries.map(([, v]) => v));
  return entries
    .sort((a, b) => b[1] - a[1])
    .map(([k, v]) => `<div class="bar-row"><span class="k">${k || "—"}</span><span class="b"><span style="width:${(v / max) * 100}%"></span></span><span class="n">${v}</span></div>`)
    .join("");
}

// ---------- Collection ----------
let setsCache = [];
async function loadCollection() {
  if (!setsCache.length) {
    setsCache = await api.get("sets");
    const sel = document.getElementById("col-set");
    sel.innerHTML = '<option value="">All sets</option>' + setsCache.map((s) => `<option>${s.name}</option>`).join("");
  }
  await refreshCollection();
}

async function refreshCollection() {
  const q = document.getElementById("col-search").value.trim();
  const set = document.getElementById("col-set").value;
  const owned = document.getElementById("col-owned").value;
  const rarity = document.getElementById("col-rarity").value;
  const params = new URLSearchParams();
  if (q) params.set("q", q);
  if (set) params.set("set", set);
  if (owned) params.set("owned", owned);
  if (rarity) params.set("rarity", rarity);
  colCards = await api.get("cards?" + params.toString());
  renderCollection();
}

// Column sorting (client-side, number-aware for collector number).
let colCards = [];
let colSort = { key: "collector_number", dir: 1 };
const RARITY_ORDER = { C: 0, Common: 0, U: 1, Uncommon: 1, R: 2, Rare: 2, M: 3, Mythic: 3 };

function numish(v) {
  const n = parseInt(String(v).replace(/\D/g, ""), 10);
  return isNaN(n) ? Infinity : n;
}

function compareCards(a, b, key) {
  if (key === "collector_number") {
    return numish(a.collector_number) - numish(b.collector_number) ||
      String(a.collector_number).localeCompare(String(b.collector_number));
  }
  if (key === "rarity") {
    return (RARITY_ORDER[a.rarity] ?? 99) - (RARITY_ORDER[b.rarity] ?? 99);
  }
  if (key === "aragorn_synergy" || key === "quantity") {
    return (a[key] || 0) - (b[key] || 0);
  }
  return String(a[key] || "").localeCompare(String(b[key] || ""));
}

function renderCollection() {
  const sorted = [...colCards].sort((a, b) => compareCards(a, b, colSort.key) * colSort.dir);
  const tbody = document.querySelector("#col-table tbody");
  tbody.innerHTML = sorted
    .map(
      (c) => `<tr data-id="${c.id}">
        <td>${c.set_name}</td><td>${c.collector_number}</td>
        <td><button class="card-link" data-name="${encodeURIComponent(c.card_name)}">${c.card_name}</button> ${c.legendary ? "⭐" : ""}</td>
        <td>${c.rarity}</td><td>${c.colour}</td><td>${c.card_type}</td>
        <td>${c.aragorn_synergy || ""}</td>
        <td><div class="qty">
          <button data-act="dec">−</button>
          <input type="number" min="0" value="${c.quantity}" />
          <button data-act="inc">+</button>
          ${c.quantity === 0 ? `<button class="link" data-act="wish">+wishlist</button>` : ""}
        </div></td>
      </tr>`
    )
    .join("");
  document.querySelectorAll("#col-table thead th.sortable").forEach((th) => {
    const active = th.dataset.sort === colSort.key;
    th.setAttribute("aria-sort", active ? (colSort.dir === 1 ? "ascending" : "descending") : "none");
    th.dataset.arrow = active ? (colSort.dir === 1 ? " ▲" : " ▼") : "";
  });
}

document.querySelector("#col-table thead").addEventListener("click", (e) => {
  const th = e.target.closest("th");
  if (!th || !th.dataset.sort) return;
  if (colSort.key === th.dataset.sort) colSort.dir *= -1;
  else colSort = { key: th.dataset.sort, dir: 1 };
  renderCollection();
});


document.querySelector("#col-table tbody").addEventListener("click", async (e) => {
  if (e.target.classList.contains("card-link")) {
    openCardModal(decodeURIComponent(e.target.dataset.name));
    return;
  }
  const tr = e.target.closest("tr");
  if (!tr) return;
  const id = tr.dataset.id;
  const input = tr.querySelector("input");
  const act = e.target.dataset.act;
  if (act === "inc") input.value = +input.value + 1;
  else if (act === "dec") input.value = Math.max(0, +input.value - 1);
  else if (act === "wish") { await addWishlist(+id); return; }
  else return;
  await api.send("PATCH", `collection/${id}`, { quantity: +input.value });
});

document.querySelector("#col-table tbody").addEventListener("change", async (e) => {
  if (e.target.tagName !== "INPUT") return;
  const id = e.target.closest("tr").dataset.id;
  await api.send("PATCH", `collection/${id}`, { quantity: Math.max(0, +e.target.value) });
});

["col-search", "col-set", "col-owned", "col-rarity"].forEach((id) => {
  const el = document.getElementById(id);
  el.addEventListener(el.tagName === "INPUT" ? "input" : "change", debounce(refreshCollection, 250));
});

// ---------- Sets ----------
async function loadSets() {
  const sets = await api.get("sets");
  document.getElementById("sets-cards").innerHTML = sets
    .map((s) => stat(s.name, `${s.unique_owned}/${s.canonical_total}`, `${s.completion}% complete`, s.completion))
    .join("");
}

// ---------- Wishlist ----------
async function addWishlist(cardId) {
  await api.send("POST", "wishlist", { card_id: cardId, purpose: "deck", priority: "P2" });
  alert("Added to wishlist.");
}

async function loadWishlist() {
  const items = await api.get("wishlist");
  const tbody = document.querySelector("#wish-table tbody");
  const P = ["P1", "P2", "P3", "P4", "Watch"];
  tbody.innerHTML = items
    .map(
      (w) => `<tr data-id="${w.id}">
        <td>${w.card.card_name} <span class="hint">(${w.card.set_name})</span></td>
        <td><select data-f="purpose">${opts(["deck", "collection", "collector"], w.purpose)}</select></td>
        <td><select data-f="priority">${opts(P, w.priority)}</select></td>
        <td><input data-f="target_price" type="number" step="0.01" value="${w.target_price}" style="width:70px" /></td>
        <td><input data-f="max_price" type="number" step="0.01" value="${w.max_price}" style="width:70px" /></td>
        <td><select data-f="status">${opts(["open", "bought", "dropped"], w.status)}</select></td>
        <td><button class="link danger" data-act="del">remove</button></td>
      </tr>`
    )
    .join("");
}

document.querySelector("#wish-table tbody").addEventListener("change", async (e) => {
  const tr = e.target.closest("tr");
  const id = tr.dataset.id;
  const payload = { card_id: 0 };
  tr.querySelectorAll("[data-f]").forEach((el) => {
    const f = el.dataset.f;
    payload[f] = el.type === "number" ? +el.value : el.value;
  });
  // card_id required by schema but unchanged server-side; fetch from row not needed.
  const items = await api.get("wishlist");
  const cur = items.find((i) => i.id == id);
  payload.card_id = cur.card.id;
  await api.send("PATCH", `wishlist/${id}`, payload);
});

document.querySelector("#wish-table tbody").addEventListener("click", async (e) => {
  if (e.target.dataset.act !== "del") return;
  const id = e.target.closest("tr").dataset.id;
  await api.send("DELETE", `wishlist/${id}`);
  loadWishlist();
});

// ---------- Deck ----------
async function loadDeck() {
  const [cards, val] = await Promise.all([
    api.get("decks/aragorn"),
    api.get("decks/aragorn/validation"),
  ]);
  document.getElementById("deck-stats").innerHTML = [
    stat("Slots", `${val.total_cards}/${val.target}`),
    stat("Owned", val.owned_slots),
    stat("To buy", val.need_slots),
    stat("Valid", val.valid ? "✅" : "❌"),
  ].join("");

  const list = (arr, cls) =>
    arr.length ? `<ul class="${cls}">` + arr.map((x) => `<li>${x}</li>`).join("") + "</ul>" : "";
  document.getElementById("deck-validation").innerHTML =
    `<h3>Validation</h3>` +
    (val.errors.length ? `<div class="err"><strong>Errors</strong>${list(val.errors, "err")}</div>` : `<p class="ok">No blocking errors.</p>`) +
    (val.warnings.length ? `<div><strong>Warnings</strong>${list(val.warnings, "hint")}</div>` : "");

  const tbody = document.querySelector("#deck-table tbody");
  tbody.innerHTML = cards
    .map(
      (d) => `<tr data-id="${d.id}">
        <td class="deck-name">
          <img class="deck-thumb" data-name="${encodeURIComponent(d.card.card_name)}" alt="" />
          <button class="card-link" data-name="${encodeURIComponent(d.card.card_name)}">${d.card.card_name}</button>
        </td>
        <td>${d.role || ""}</td>
        <td>${d.card.quantity > 0 ? '<span class="pill owned">owned</span>' : '<span class="pill missing">need</span>'}</td>
        <td>${d.status}</td>
        <td>${d.is_commander ? "👑" : ""}</td>
        <td><button class="link danger" data-act="del">remove</button></td>
      </tr>`
    )
    .join("");
  setupLazyThumbs("#deck-table");
}

// Lazy-load Scryfall thumbnails only for rows scrolled into view (avoids
// hammering the Scryfall API with all cards at once).
let thumbObserver = null;
function setupLazyThumbs(rootSelector) {
  if (thumbObserver) thumbObserver.disconnect();
  thumbObserver = new IntersectionObserver((entries, obs) => {
    entries.forEach((en) => {
      if (en.isIntersecting) {
        loadThumb(en.target);
        obs.unobserve(en.target);
      }
    });
  }, { rootMargin: "200px" });
  document.querySelectorAll(`${rootSelector} img.deck-thumb`).forEach((el) => thumbObserver.observe(el));
}

async function loadThumb(img) {
  try {
    const c = await fetchScryfall(decodeURIComponent(img.dataset.name));
    const u =
      (c.image_uris && c.image_uris.small) ||
      (c.card_faces && c.card_faces[0].image_uris && c.card_faces[0].image_uris.small);
    if (u) img.src = u;
    else img.classList.add("thumb-missing");
  } catch (e) {
    img.classList.add("thumb-missing");
  }
}

document.querySelector("#deck-table tbody").addEventListener("click", async (e) => {
  if (e.target.classList.contains("card-link") || e.target.classList.contains("deck-thumb")) {
    openCardModal(decodeURIComponent(e.target.dataset.name));
    return;
  }
  if (e.target.dataset.act !== "del") return;
  const id = e.target.closest("tr").dataset.id;
  await api.send("DELETE", `decks/aragorn/cards/${id}`);
  loadDeck();
});

// ---------- Deck import (paste decklist) ----------
document.getElementById("deck-import-btn").addEventListener("click", async () => {
  const text = document.getElementById("deck-import-text").value;
  const replace = document.getElementById("deck-import-replace").checked;
  const out = document.getElementById("deck-import-result");
  if (!text.trim()) { out.textContent = "Incolla prima una lista."; return; }
  out.textContent = "Importazione\u2026";
  try {
    const data = await api.send("POST", "decks/aragorn/import", { text, replace });
    out.textContent = JSON.stringify(data, null, 2);
    loadDeck();
  } catch (err) {
    out.textContent = "Errore: " + err.message;
  }
});

// ---------- Import ----------
document.getElementById("import-btn").addEventListener("click", async () => {
  const file = document.getElementById("import-file").files[0];
  const out = document.getElementById("import-result");
  if (!file) { out.textContent = "Choose a file first."; return; }
  out.textContent = "Importing…";
  const fd = new FormData();
  fd.append("file", file);
  try {
    const r = await fetch(API + "import", { method: "POST", body: fd });
    const data = await r.json();
    out.textContent = JSON.stringify(data, null, 2);
    setsCache = [];
  } catch (err) {
    out.textContent = "Error: " + err.message;
  }
});

// ---------- Scryfall enrichment (fix Unknown sets) ----------
document.getElementById("enrich-btn").addEventListener("click", async () => {
  const btn = document.getElementById("enrich-btn");
  const out = document.getElementById("enrich-result");
  btn.disabled = true;
  out.textContent = "Recupero dati da Scryfall… (può richiedere un minuto)";
  try {
    const data = await api.send("POST", "cards/enrich", null);
    out.textContent = JSON.stringify(data, null, 2);
    setsCache = [];
  } catch (err) {
    out.textContent = "Errore: " + err.message;
  } finally {
    btn.disabled = false;
  }
});

// ---------- helpers ----------
function opts(list, sel) {
  return list.map((o) => `<option ${o === sel ? "selected" : ""}>${o}</option>`).join("");
}
function debounce(fn, ms) {
  let t;
  return (...a) => { clearTimeout(t); t = setTimeout(() => fn(...a), ms); };
}

// ---------- Card modal (Scryfall image + Cardmarket link) ----------
const scryfallCache = new Map();

function cardmarketUrl(name) {
  // Cardmarket search for the card name (Magic Single).
  return "https://www.cardmarket.com/en/Magic/Products/Search?searchString=" + encodeURIComponent(name);
}

async function fetchScryfall(name) {
  if (scryfallCache.has(name)) return scryfallCache.get(name);
  const url = "https://api.scryfall.com/cards/named?fuzzy=" + encodeURIComponent(name);
  const res = await fetch(url);
  if (!res.ok) throw new Error("Scryfall: carta non trovata");
  const data = await res.json();
  scryfallCache.set(name, data);
  return data;
}

async function openCardModal(name) {
  const overlay = document.getElementById("card-modal");
  const img = document.getElementById("modal-img");
  const nameEl = document.getElementById("modal-name");
  const typeEl = document.getElementById("modal-type");
  const oracleEl = document.getElementById("modal-oracle");
  const scry = document.getElementById("modal-scryfall");
  const cm = document.getElementById("modal-cardmarket");

  nameEl.textContent = name;
  typeEl.textContent = "";
  oracleEl.textContent = "Caricamento da Scryfall…";
  img.removeAttribute("src");
  scry.href = "https://scryfall.com/search?q=" + encodeURIComponent('!"' + name + '"');
  cm.href = cardmarketUrl(name);
  overlay.hidden = false;

  try {
    const c = await fetchScryfall(name);
    const imgUri =
      (c.image_uris && c.image_uris.normal) ||
      (c.card_faces && c.card_faces[0].image_uris && c.card_faces[0].image_uris.normal);
    if (imgUri) img.src = imgUri;
    nameEl.textContent = c.name || name;
    typeEl.textContent = [c.type_line, c.mana_cost].filter(Boolean).join("  •  ");
    oracleEl.textContent = c.oracle_text || (c.card_faces ? c.card_faces.map((f) => f.oracle_text).join("\n//\n") : "");
    if (c.scryfall_uri) scry.href = c.scryfall_uri;
    if (c.purchase_uris && c.purchase_uris.cardmarket) cm.href = c.purchase_uris.cardmarket;
  } catch (err) {
    oracleEl.textContent = "Non trovata su Scryfall. Usa i link qui sotto per cercarla.";
  }
}

function closeCardModal() {
  document.getElementById("card-modal").hidden = true;
}
document.getElementById("modal-close").addEventListener("click", closeCardModal);
document.getElementById("card-modal").addEventListener("click", (e) => {
  if (e.target.id === "card-modal") closeCardModal();
});
document.addEventListener("keydown", (e) => {
  if (e.key === "Escape") closeCardModal();
});

loadDashboard();
