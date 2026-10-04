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
  const loaders = { dashboard: loadDashboard, collection: loadCollection, stats: loadStats, sets: loadSets, wishlist: loadWishlist, deck: loadDeck };
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
    await refreshSetOptions();
  }
  await refreshCollection();
}

async function refreshSetOptions() {
  const sel = document.getElementById("col-set");
  const selectedSet = sel.value;
  setsCache = await api.get("sets");
  sel.innerHTML = '<option value="">All sets</option>' + setsCache.map((s) => `<option>${s.name}</option>`).join("");
  sel.value = selectedSet;
}

function collectionFilterParams() {
  const q = document.getElementById("col-search").value.trim();
  const set = document.getElementById("col-set").value;
  const owned = document.getElementById("col-owned").value;
  const rarity = document.getElementById("col-rarity").value;
  const colour = document.getElementById("col-colour").value;
  const cardType = document.getElementById("col-type").value;
  const edition = document.getElementById("col-edition").value;
  const params = new URLSearchParams();
  if (q) params.set("q", q);
  if (set) params.set("set", set);
  if (owned) params.set("owned", owned);
  if (rarity) params.set("rarity", rarity);
  if (colour) params.set("colour", colour);
  if (cardType) params.set("card_type", cardType);
  if (edition) params.set("edition", edition);
  return params;
}

async function refreshCollection() {
  colCards = await api.get("cards?" + collectionFilterParams().toString());
  renderCollection();
  loadPrices(); // one batched call; fills the price cells when it resolves
}

// Client-side sorting + display helpers.
const EDITION_LABEL = { ltr: "LOTR", ltc: "LOTR Cmd", hob: "Hobbit", hoc: "Hobbit Etl" };
let colCards = [];
let colSort = { key: "collector_number", dir: 1 };
const RARITY_ORDER = { C: 0, Common: 0, U: 1, Uncommon: 1, R: 2, Rare: 2, M: 3, Mythic: 3 };

function numish(v) {
  const n = parseInt(String(v).replace(/\D/g, ""), 10);
  return isNaN(n) ? Infinity : n;
}

function compareCards(a, b, key) {
  if (key === "cardmarket") {
    const priceA = priceValue(priceMap[a.id]);
    const priceB = priceValue(priceMap[b.id]);
    if (priceA == null && priceB != null) return 1;
    if (priceA != null && priceB == null) return -1;
    if (priceA != null && priceB != null) {
      return (priceA - priceB) * colSort.dir ||
        a.card_name.localeCompare(b.card_name) * colSort.dir;
    }
    return a.card_name.localeCompare(b.card_name) * colSort.dir;
  }
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
  const sorted = [...colCards].sort((a, b) =>
    colSort.key === "cardmarket"
      ? compareCards(a, b, colSort.key)
      : compareCards(a, b, colSort.key) * colSort.dir
  );
  const tbody = document.querySelector("#col-table tbody");
  tbody.innerHTML = sorted
    .map(
      (c) => `<tr data-id="${c.id}">
        <td>${c.set_name}</td>
        <td><span class="ed-badge">${EDITION_LABEL[c.edition] || c.edition || "—"}</span></td>
        <td>${c.collector_number}</td>
        <td><button class="card-link" data-name="${encodeURIComponent(c.card_name)}">${c.card_name}</button> ${c.legendary ? "⭐" : ""}</td>
        <td>${c.rarity}</td><td>${c.colour}</td><td>${c.card_type}</td>
        <td class="price-cell" data-id="${c.id}" data-name="${encodeURIComponent(c.card_name)}">${priceCell(c)}</td>
        <td class="quantity-cell"><div class="qty">
          <button data-act="dec">−</button>
          <input type="number" min="0" value="${c.quantity}" />
          <button data-act="inc">+</button>
        </div></td>
        <td class="collection-action-cell">${c.quantity === 0 ? `<button class="link" data-act="wish">+ wishlist</button>` : ""}</td>
        <td class="collection-action-cell"><button class="link danger" data-act="delete" aria-label="Delete ${c.card_name}" title="Delete this card entry">delete</button></td>
      </tr>`
    )
    .join("");
  document.querySelectorAll("#col-table thead th.sortable").forEach((th) => {
    const active = th.dataset.sort === colSort.key;
    th.setAttribute("aria-sort", active ? (colSort.dir === 1 ? "ascending" : "descending") : "none");
    th.dataset.arrow = active ? (colSort.dir === 1 ? " ▲" : " ▼") : "";
  });
}

// Cardmarket prices for the whole filtered table, fetched in ONE backend call
// (the server resolves them 75-at-a-time against Scryfall and caches them).
// Doing this per row would mean hundreds of requests and would stall the page.
let priceMap = {};

// Cardmarket prices are in EUR; the backend adds a GBP conversion (ECB rate).
function priceValue(p) {
  return p ? p.gbp ?? p.eur ?? null : null;
}

function priceLink(p, href) {
  if (!p || p.eur == null) return `<a href="${href}" target="_blank" rel="noopener" class="hint">—</a>`;
  const text = p.gbp != null ? `£${p.gbp.toFixed(2)}` : `€${p.eur.toFixed(2)}`;
  return `<a href="${href}" target="_blank" rel="noopener" class="price-link" title="Cardmarket €${p.eur.toFixed(2)}">${text}</a>`;
}

function priceCell(c) {
  const p = priceMap[c.id];
  if (p === undefined) return '<span class="hint">…</span>';
  return priceLink(p, (p && p.url) || cardmarketUrl(c.card_name));
}

async function loadPrices() {
  const token = ++priceToken;
  priceMap = {};
  try {
    const data = await api.get("cards/prices?" + collectionFilterParams().toString());
    if (token !== priceToken) return; // a newer filter change superseded this
    priceMap = data;
  } catch (e) {
    priceMap = {};
  }
  if (colSort.key === "cardmarket") renderCollection();
  document.querySelectorAll("#col-table td.price-cell").forEach((cell) => {
    const p = priceMap[cell.dataset.id];
    const name = decodeURIComponent(cell.dataset.name);
    const href = (p && p.url) || cardmarketUrl(name);
    cell.innerHTML = priceLink(p, href);
  });
}
let priceToken = 0;

document.querySelector("#col-table thead").addEventListener("click", (e) => {
  const th = e.target.closest("th");
  if (!th || !th.dataset.sort || !th.classList.contains("sortable")) return;
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
  else if (act === "delete") {
    const card = colCards.find((c) => c.id === +id);
    if (!card || !confirm(`Permanently delete "${card.card_name}" (${card.set_name}) from the catalogue? Its deck entries and wishlist entries will also be removed.`)) return;
    try {
      await api.send("DELETE", `collection/${id}`);
      await refreshSetOptions();
      await refreshCollection();
    } catch (err) {
      alert("Error deleting card: " + err.message);
    }
    return;
  }
  else return;
  await api.send("PATCH", `collection/${id}`, { quantity: +input.value });
});

document.querySelector("#col-table tbody").addEventListener("change", async (e) => {
  if (e.target.tagName !== "INPUT") return;
  const id = e.target.closest("tr").dataset.id;
  await api.send("PATCH", `collection/${id}`, { quantity: Math.max(0, +e.target.value) });
});

["col-search", "col-set", "col-owned", "col-rarity", "col-colour", "col-type", "col-edition"].forEach((id) => {
  const el = document.getElementById(id);
  el.addEventListener(el.tagName === "INPUT" ? "input" : "change", debounce(refreshCollection, 250));
});

// ---------- Collection export (filtered list: AI / Cardmarket / CSV) ----------
document.getElementById("col-export-btn").addEventListener("click", async () => {
  const status = document.getElementById("col-export-status");
  const ta = document.getElementById("col-export-text");
  const params = collectionFilterParams();
  params.set("fmt", document.getElementById("col-export-format").value);
  params.set("qty", document.getElementById("col-export-qty").value);
  status.textContent = "Generazione\u2026";
  try {
    const r = await fetch(`${API}cards/export?` + params.toString());
    if (!r.ok) throw new Error(await r.text());
    ta.value = await r.text();
    status.textContent = ta.value.trim() ? "" : "Nessuna carta per questo filtro.";
  } catch (err) {
    ta.value = "";
    status.textContent = "Errore: " + err.message;
  }
});
document.getElementById("col-export-copy").addEventListener("click", async () => {
  const ta = document.getElementById("col-export-text");
  const status = document.getElementById("col-export-status");
  if (!ta.value) { status.textContent = "Genera prima la lista."; return; }
  try {
    await navigator.clipboard.writeText(ta.value);
  } catch {
    ta.select();
    document.execCommand("copy");
  }
  status.textContent = "Copiato \u2705";
});
document.getElementById("col-export-download").addEventListener("click", () => {
  const ta = document.getElementById("col-export-text");
  const status = document.getElementById("col-export-status");
  if (!ta.value) { status.textContent = "Genera prima la lista."; return; }
  const fmt = document.getElementById("col-export-format").value;
  const ext = fmt === "csv" ? "csv" : "txt";
  const blob = new Blob([ta.value], { type: fmt === "csv" ? "text/csv" : "text/plain" });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = `collection-${fmt}.${ext}`;
  document.body.appendChild(a);
  a.click();
  a.remove();
  URL.revokeObjectURL(url);
  status.textContent = "Scaricato \u2705";
});

// ---------- Stats ----------
// Colour palettes for pie segments (keyed by category value).
const STAT_COLOURS = {
  owned: { Owned: "#3f9d5a", Missing: "#8a3b34" },
  rarity: { C: "#9aa0a6", U: "#c0c7ce", R: "#d9b34a", M: "#e06a2c", S: "#8e7cc3", "—": "#5a5f66" },
  colour: { W: "#f5e7c4", U: "#3b82d6", B: "#5a5560", R: "#d0433a", G: "#3f9d5a", Multicolour: "#d9b34a", Colourless: "#9aa0a6", "—": "#5a5f66" },
  type: { Creature: "#3f9d5a", Instant: "#3b82d6", Sorcery: "#d0433a", Enchantment: "#c9a227", Artifact: "#9aa0a6", Planeswalker: "#e06a2c", Land: "#8a6d3b", Battle: "#b5533c", Other: "#5a5f66" },
  edition: { ltr: "#d9b34a", ltc: "#c98a2c", hob: "#3f9d5a", hoc: "#2f7d46", "—": "#5a5f66" },
  set: { "The Lord of the Rings": "#d9b34a", "The Hobbit": "#3f9d5a", "—": "#5a5f66" },
};
const PALETTE = ["#d9b34a", "#3f9d5a", "#3b82d6", "#d0433a", "#e06a2c", "#8e7cc3", "#9aa0a6", "#2f7d46", "#c98a2c", "#5a5f66"];
const EDITION_FULL = { ltr: "LOTR", ltc: "LOTR Commander", hob: "Hobbit", hoc: "Hobbit Eternal" };

function colourFor(cat, key, i) {
  return (STAT_COLOURS[cat] && STAT_COLOURS[cat][key]) || PALETTE[i % PALETTE.length];
}

// A single conic-gradient pie with a legend (value + %).
function pie(cat, title, obj) {
  const entries = Object.entries(obj).filter(([, v]) => v > 0).sort((a, b) => b[1] - a[1]);
  const total = entries.reduce((s, [, v]) => s + v, 0);
  if (!total) return `<div class="pie-card"><h4>${title}</h4><p class="hint">—</p></div>`;
  let acc = 0;
  const segs = entries.map(([k, v], i) => {
    const start = (acc / total) * 360;
    acc += v;
    const end = (acc / total) * 360;
    return `${colourFor(cat, k, i)} ${start}deg ${end}deg`;
  });
  const legend = entries
    .map(([k, v], i) => {
      const label = cat === "edition" ? (EDITION_FULL[k] || k) : k;
      const pct = Math.round((v / total) * 100);
      return `<li><span class="dot" style="background:${colourFor(cat, k, i)}"></span>${label} <b>${v}</b> <span class="hint">(${pct}%)</span></li>`;
    })
    .join("");
  return `<div class="pie-card">
    <h4>${title} <span class="hint">· ${total}</span></h4>
    <div class="pie" style="background:conic-gradient(${segs.join(",")})"></div>
    <ul class="pie-legend">${legend}</ul>
  </div>`;
}

// Owned vs total pair of pies for one dimension.
function pieGroup(cat, heading, block) {
  return `<div class="panel">
    <h3>${heading}</h3>
    <div class="pie-row">
      ${pie(cat, "Owned", block.owned)}
      ${pie(cat, "In collection", block.total)}
    </div>
  </div>`;
}

async function loadStats() {
  const s = await api.get("stats");
  document.getElementById("stats-headline").innerHTML = [
    stat("Owned (unique)", `${s.unique_owned}/${s.unique_total}`, `${s.completion}% del catalogo`, s.completion),
    stat("Total copies", s.total_copies, "physical cards"),
    stat("Missing (unique)", s.unique_missing, "catalogued not owned"),
    stat("Mythics owned", `${(s.by_rarity.owned.M || 0)}/${(s.by_rarity.total.M || 0)}`, "rare mitiche"),
  ].join("");
  document.getElementById("stats-charts").innerHTML = [
    `<div class="panel"><h3>Owned vs Missing</h3><div class="pie-row">${pie("owned", "Unique cards", { Owned: s.unique_owned, Missing: s.unique_missing })}</div></div>`,
    pieGroup("rarity", "By rarity", s.by_rarity),
    pieGroup("colour", "By colour", s.by_colour),
    pieGroup("type", "By type", s.by_type),
    pieGroup("edition", "By edition", s.by_edition),
    pieGroup("set", "By set", s.by_set),
  ].join("");
}

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
let decksCache = [];
let currentDeckSlug = null;
let deckViewMode = "visual";
const DV_LABEL = { Commander: "👑 Comandante", Creature: "Creature", Instant: "Istantanei", Sorcery: "Stregonerie", Artifact: "Artefatti", Enchantment: "Incantesimi", Planeswalker: "Planeswalker", Battle: "Battaglie", Land: "Terre", Other: "Altro" };
const MANA_COL = { W: "#f6f3d6", U: "#a9cbe8", B: "#b7afac", R: "#e79a86", G: "#96cfa6", C: "#cfc9c2" };
const BASIC_NAMES = new Set(["plains", "island", "swamp", "mountain", "forest", "wastes"]);
function isBasicCard(card) {
  return /basic/i.test(card.card_type || "") || BASIC_NAMES.has((card.card_name || "").trim().toLowerCase());
}

async function loadDeck() {
  decksCache = await api.get("decks");
  if (!decksCache.length) return;
  if (!currentDeckSlug || !decksCache.find((d) => d.slug === currentDeckSlug)) {
    currentDeckSlug = decksCache[0].slug;
  }
  const sel = document.getElementById("deck-select");
  sel.innerHTML = decksCache
    .map((d) => `<option value="${d.slug}" ${d.slug === currentDeckSlug ? "selected" : ""}>${d.name} · ${d.format}</option>`)
    .join("");
  await renderDeck();
}

async function renderDeck() {
  const deck = decksCache.find((d) => d.slug === currentDeckSlug);
  if (!deck) return;
  const badge = document.getElementById("deck-format-badge");
  badge.textContent =
    deck.format === "commander"
      ? "👑 Commander · singleton · 100"
      : `⚔️ Standard · min ${deck.deck_size} · max ${deck.max_copies}×`;
  document.getElementById("deck-del-btn").disabled = deck.slug === "aragorn";

  const [cards, val] = await Promise.all([
    api.get(`decks/${currentDeckSlug}/cards`),
    api.get(`decks/${currentDeckSlug}/validation`),
  ]);

  const mainLabel = val.format === "commander" ? `${val.main_cards}/${val.target}` : `${val.main_cards} (min ${val.target})`;
  document.getElementById("deck-stats").innerHTML = [
    stat("Main deck", mainLabel),
    stat("Sideboard", val.side_cards),
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
        <td>${d.board === "side" ? '<span class="pill">SB</span>' : "—"}</td>
        <td>${d.quantity}</td>
        <td>${d.role || ""}</td>
        <td>${d.card.quantity >= d.quantity || isBasicCard(d.card)
          ? `<span class="pill owned">owned</span> <span class="hint">${d.card.quantity}/${d.quantity}</span>`
          : `<span class="pill missing">need</span> <span class="hint">${d.card.quantity}/${d.quantity}</span>`}</td>
        <td>${deck.format === "commander" && d.board !== "side"
          ? `<button class="link" data-act="cmd" title="${d.is_commander ? "Rimuovi come comandante" : "Imposta come comandante"}">${d.is_commander ? "👑" : "☆"}</button>`
          : d.is_commander ? "👑" : ""}</td>
        <td><button class="link danger" data-act="del">remove</button></td>
      </tr>`
    )
    .join("");
  setupLazyThumbs("#deck-table");
  applyDeckViewMode();
  renderForgePanel();
}

// ---------- Visual deck view ----------
function manaPips(mc) {
  if (!mc) return "";
  return (mc.match(/\{[^}]+\}/g) || [])
    .map((sym) => {
      const s = sym.slice(1, -1);
      if (/^\d+$/.test(s) || ["X", "Y", "Z"].includes(s)) return `<i class="dv-pip dv-pip-c">${s}</i>`;
      const col = MANA_COL[s.split("/")[0]] || "#cfc9c2";
      return `<i class="dv-pip" style="background:${col}">${s.replace("/", "")}</i>`;
    })
    .join("");
}

async function applyDeckViewMode() {
  const vis = deckViewMode === "visual";
  document.getElementById("deck-visual").hidden = !vis;
  const tableWrap = document.querySelector("#view-deck .table-wrap");
  if (tableWrap) tableWrap.hidden = vis;
  document.getElementById("deck-mode-visual").classList.toggle("active", vis);
  document.getElementById("deck-mode-table").classList.toggle("active", !vis);
  if (vis) await renderDeckVisual();
}

async function renderDeckVisual() {
  const el = document.getElementById("deck-visual");
  if (!currentDeckSlug) { el.innerHTML = ""; return; }
  el.innerHTML = '<p class="hint">Analisi del mazzo\u2026</p>';
  let a;
  try { a = await api.get(`decks/${currentDeckSlug}/analysis`); }
  catch (e) { el.innerHTML = '<p class="err">Errore analisi: ' + e.message + "</p>"; return; }

  const pile = (cat, cards) => `
    <section class="dv-pile">
      <h4>${DV_LABEL[cat] || cat} <span>${cards.reduce((n, c) => n + c.qty, 0)}</span></h4>
      <ul>${cards
        .map((c) => `<li class="dv-card ${c.owned ? "" : "need"}" data-name="${encodeURIComponent(c.name)}">
          <span class="dv-mc">${manaPips(c.mana_cost)}</span>
          <span class="dv-name">${c.qty > 1 ? c.qty + "\u00d7 " : ""}${c.name}${c.is_commander ? " 👑" : ""}</span>
        </li>`)
        .join("")}</ul>
    </section>`;
  const board = Object.entries(a.groups).map(([cat, cards]) => pile(cat, cards)).join("");

  const curveMax = Math.max(1, ...Object.values(a.curve));
  const curveBars = Object.entries(a.curve)
    .map(([k, v]) => `<div class="dv-bar"><b>${v}</b><span class="dv-bar-fill" style="height:${Math.round(90 * v / curveMax) + 4}%"></span><i>${k}</i></div>`)
    .join("");
  const colBadges = Object.entries(a.colours)
    .filter(([, v]) => v)
    .map(([k, v]) => `<span class="dv-cbadge" style="background:${MANA_COL[k]}">${k} ${v}</span>`)
    .join("");

  const P = a.profile;
  const metric = (l, v) => `<div class="dv-metric"><span>${v}</span><label>${l}</label></div>`;
  const archetype = P.aggro_pct >= 62 ? "Aggro" : P.aggro_pct <= 42 ? "Controllo" : "Midrange";
  const strat = (a.strategy || "").replace(/</g, "&lt;");

  el.innerHTML = `
    <div class="dv-wrap">
      <div class="dv-board">${board}</div>
      <aside class="dv-side">
        <div class="dv-panel">
          <h4>Profilo — <span class="dv-arch">${archetype}</span></h4>
          <div class="dv-ad"><span class="dv-ad-att" style="width:${P.aggro_pct}%">⚔️ ${P.aggro_pct}%</span><span class="dv-ad-def">🛡️ ${100 - P.aggro_pct}%</span></div>
          <div class="dv-metrics">
            ${metric("Creature", P.creatures)}${metric("Potenza tot.", P.power)}${metric("Evasivi", P.evasion)}
            ${metric("Blocker", P.blockers)}${metric("Rimozione", P.removal)}${metric("Board wipe", P.wipes)}
            ${metric("Counter", P.counters)}${metric("Rampa", P.ramp)}${metric("Pescata", P.draw)}
            ${metric("CMC medio", P.avg_cmc)}${metric("Terre", P.lands)}${metric("Possedute", a.owned + "/" + a.total)}
          </div>
        </div>
        <div class="dv-panel">
          <h4>Curva di mana</h4>
          <div class="dv-curve">${curveBars}</div>
          <div class="dv-colours">${colBadges}</div>
        </div>
        <div class="dv-panel">
          <h4>Strategia in partita</h4>
          <textarea id="deck-strategy" rows="8" placeholder="Descrivi come pilotare il mazzo durante la gara\u2026">${strat}</textarea>
          <div class="toolbar"><button id="deck-strategy-save" class="btn">💾 Salva strategia</button><span id="deck-strategy-status" class="hint"></span></div>
        </div>
      </aside>
    </div>`;
  el.querySelectorAll(".dv-card").forEach((li) =>
    li.addEventListener("click", () => openCardModal(decodeURIComponent(li.dataset.name)))
  );
  document.getElementById("deck-strategy-save").addEventListener("click", saveStrategy);
}

async function saveStrategy() {
  const ta = document.getElementById("deck-strategy");
  const st = document.getElementById("deck-strategy-status");
  st.textContent = "Salvataggio\u2026";
  try {
    await api.send("PATCH", `decks/${currentDeckSlug}`, { notes: ta.value });
    const d = decksCache.find((x) => x.slug === currentDeckSlug);
    if (d) d.notes = ta.value;
    st.textContent = "Salvato \u2705";
  } catch (e) {
    st.textContent = "Errore: " + e.message;
  }
}

document.getElementById("deck-mode-visual").addEventListener("click", () => { deckViewMode = "visual"; applyDeckViewMode(); });
document.getElementById("deck-mode-table").addEventListener("click", () => { deckViewMode = "table"; applyDeckViewMode(); });

document.getElementById("deck-select").addEventListener("change", (e) => {
  currentDeckSlug = e.target.value;
  renderDeck();
});

document.getElementById("deck-new-btn").addEventListener("click", () => {
  document.getElementById("deck-new-form").hidden = false;
});
document.getElementById("deck-new-cancel").addEventListener("click", () => {
  document.getElementById("deck-new-form").hidden = true;
});
document.getElementById("deck-new-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const name = document.getElementById("deck-new-name").value.trim();
  if (!name) return;
  const format = document.getElementById("deck-new-format").value;
  const allowed_colours = document.getElementById("deck-new-colours").value.trim().toUpperCase();
  try {
    const deck = await api.send("POST", "decks", { name, format, allowed_colours });
    currentDeckSlug = deck.slug;
    document.getElementById("deck-new-form").hidden = true;
    document.getElementById("deck-new-name").value = "";
    document.getElementById("deck-new-colours").value = "";
    await loadDeck();
  } catch (err) {
    alert("Errore: " + err.message);
  }
});

document.getElementById("deck-del-btn").addEventListener("click", async () => {
  const deck = decksCache.find((d) => d.slug === currentDeckSlug);
  if (!deck || deck.slug === "aragorn") return;
  if (!confirm(`Eliminare il mazzo "${deck.name}"? Le carte restano in collezione.`)) return;
  await api.send("DELETE", `decks/${currentDeckSlug}`);
  currentDeckSlug = null;
  await loadDeck();
});

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
    if (c.image_small) img.src = c.image_small;
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
  if (e.target.dataset.act === "cmd") {
    const id = e.target.closest("tr").dataset.id;
    try {
      const slot = await api.send("POST", `decks/${currentDeckSlug}/cards/${id}/commander`);
      const d = decksCache.find((x) => x.slug === currentDeckSlug);
      if (d) d.commander_name = slot.is_commander ? slot.card.card_name : "";
    } catch (err) {
      alert("Errore: " + err.message);
    }
    renderDeck();
    return;
  }
  if (e.target.dataset.act !== "del") return;
  const id = e.target.closest("tr").dataset.id;
  await api.send("DELETE", `decks/${currentDeckSlug}/cards/${id}`);
  renderDeck();
});

// ---------- Deck import (paste decklist) ----------
document.getElementById("deck-import-btn").addEventListener("click", async () => {
  const text = document.getElementById("deck-import-text").value;
  const replace = document.getElementById("deck-import-replace").checked;
  const board = document.getElementById("deck-import-board").value;
  const out = document.getElementById("deck-import-result");
  if (!currentDeckSlug) { out.textContent = "Seleziona o crea prima un mazzo."; return; }
  if (!text.trim()) { out.textContent = "Incolla prima una lista."; return; }
  out.textContent = "Importazione\u2026";
  try {
    const data = await api.send("POST", `decks/${currentDeckSlug}/import`, { text, replace, board });
    out.textContent = JSON.stringify(data, null, 2);
    renderDeck();
  } catch (err) {
    out.textContent = "Errore: " + err.message;
  }
});

// ---------- Deck export (AI decklist / Cardmarket buy-list) ----------
async function generateDeckExport() {
  const status = document.getElementById("deck-export-status");
  const ta = document.getElementById("deck-export-text");
  if (!currentDeckSlug) { status.textContent = "Seleziona prima un mazzo."; return; }
  const scope = document.getElementById("deck-export-scope").value;
  const board = document.getElementById("deck-export-board").value;
  const fmt = document.getElementById("deck-export-format").value;
  status.textContent = "Generazione\u2026";
  try {
    const r = await fetch(`${API}decks/${currentDeckSlug}/export?scope=${scope}&board=${board}&fmt=${fmt}`);
    if (!r.ok) throw new Error(await r.text());
    ta.value = await r.text();
    status.textContent = ta.value.trim() ? "" : "Nessuna carta per questo filtro.";
  } catch (err) {
    ta.value = "";
    status.textContent = "Errore: " + err.message;
  }
}
document.getElementById("deck-export-btn").addEventListener("click", generateDeckExport);
document.getElementById("deck-export-copy").addEventListener("click", async () => {
  const ta = document.getElementById("deck-export-text");
  const status = document.getElementById("deck-export-status");
  if (!ta.value) { status.textContent = "Genera prima la lista."; return; }
  try {
    await navigator.clipboard.writeText(ta.value);
  } catch {
    ta.select();
    document.execCommand("copy");
  }
  status.textContent = "Copiato \u2705";
});
document.getElementById("deck-export-download").addEventListener("click", () => {
  const ta = document.getElementById("deck-export-text");
  const status = document.getElementById("deck-export-status");
  if (!ta.value) { status.textContent = "Genera prima la lista."; return; }
  const fmt = document.getElementById("deck-export-format").value;
  const scope = document.getElementById("deck-export-scope").value;
  const deck = decksCache.find((d) => d.slug === currentDeckSlug);
  const safe = (deck ? deck.name : "deck").replace(/[^a-z0-9]+/gi, "-").toLowerCase().replace(/^-+|-+$/g, "");
  const ext = fmt === "csv" ? "csv" : "txt";
  const blob = new Blob([ta.value], { type: fmt === "csv" ? "text/csv" : "text/plain" });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = `${safe}-${scope}.${ext}`;
  document.body.appendChild(a);
  a.click();
  a.remove();
  URL.revokeObjectURL(url);
  status.textContent = "Scaricato \u2705";
});

// ---------- Forge engine (deck testing on the Raspberry Pi) ----------
let forgeTestDecks = null;
let forgeTimer = null;
let forgeOpenJob = null;
const FORGE_ACTIVE = new Set(["QUEUED", "RUNNING"]);
const FORGE_STATUS_PILL = { COMPLETED: "owned", FAILED: "missing", TIMEOUT: "missing", CANCELLED: "", QUEUED: "warn", RUNNING: "warn" };
const FORGE_FLAG_LABEL = {
  unsupported_cards: "⛔ carte non supportate",
  ai_fallback: "⚠️ AI fallback",
  clock_draws: "⏱️ patte per tempo",
  structure_invalid: "⚠️ mazzo non valido",
  small_sample: "📉 campione piccolo",
};

function esc(s) {
  return String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}

function forgeError(err) {
  try {
    const body = JSON.parse(err.message);
    const d = body.detail ?? body;
    if (typeof d === "string") return d;
    const issues = (d.issues || []).map((i) => i.message).join("; ");
    return (d.detail || JSON.stringify(d)) + (issues ? ` — ${issues}` : "");
  } catch {
    return err.message;
  }
}

async function renderForgePanel() {
  const deck = decksCache.find((d) => d.slug === currentDeckSlug);
  if (!deck) return;
  const st = document.getElementById("forge-status");
  const status = await api.get("forge/status").catch((e) => ({ status: "unavailable", error: e.message }));
  const online = status.status !== "unavailable";
  st.innerHTML = online
    ? `<span class="pill ${status.status === "ready" ? "owned" : "warn"}">${esc(status.status)}</span> ` +
      `Forge ${esc(status.forge_version)} · Java ${esc(status.java_version)} · ${esc(status.platform)} · ` +
      `worker ${esc(status.worker)} · in coda ${status.queue}`
    : `<span class="pill missing">offline</span> Motore Forge non raggiungibile: ${esc(status.error)}`;
  ["forge-audit-btn", "forge-sim-btn"].forEach((id) => (document.getElementById(id).disabled = !online));

  if (online && !forgeTestDecks) forgeTestDecks = await api.get("forge/test-decks").catch(() => null);
  const commander = deck.format === "commander";
  const sel = document.getElementById("forge-opponent");
  const previous = sel.value;
  const mine = decksCache.filter((d) => (d.format === "commander") === commander);
  const tests = (forgeTestDecks || []).filter((t) => (t.format === "commander") === commander);
  sel.innerHTML =
    `<optgroup label="I miei mazzi">` +
    mine.map((d) => `<option value="deck:${esc(d.slug)}">${esc(d.name)}${d.slug === deck.slug ? " (mirror)" : ""}</option>`).join("") +
    `</optgroup>` +
    (tests.length
      ? `<optgroup label="Mazzi di riferimento Forge">` +
        tests.map((t) => `<option value="test:${esc(t.name)}">${esc(t.name.replace(/_/g, " "))}</option>`).join("") +
        `</optgroup>`
      : "");
  if ([...sel.options].some((o) => o.value === previous)) {
    sel.value = previous;
  } else {
    const firstOther = [...sel.options].find((o) => o.value !== `deck:${deck.slug}`);
    if (firstOther) sel.value = firstOther.value;
  }
  updateForgeDckLink();
  if (online) await refreshForgeJobs();
}

function updateForgeDckLink() {
  const scope = document.getElementById("forge-scope").value;
  document.getElementById("forge-dck-link").href = `${API}decks/${currentDeckSlug}/forge.dck?scope=${scope}`;
}

function forgeMySide(job, slug) {
  const sides = (job.decks || []).filter((d) => d.deck_ref === slug).map((d) => d.side);
  return sides.length === 1 ? sides[0] : "a";
}

function forgeOpponent(job, slug) {
  if (job.kind === "audit") return "— (self-test)";
  const mine = forgeMySide(job, slug);
  const other = (job.decks || []).find((d) => d.side !== mine);
  return other ? other.deck_name.replace(/_/g, " ") : "?";
}

function forgeResult(job, slug) {
  if (job.kind === "audit") {
    const a = job.result?.audit;
    if (!a) return "";
    const cls = a.status === "PASS" ? "owned" : a.status === "WARN" ? "warn" : "missing";
    return `<span class="pill ${cls}">${a.status}</span>`;
  }
  const done = job.games_completed;
  if (!done) return "";
  const mineIsB = forgeMySide(job, slug) === "b";
  const w = mineIsB ? job.deck_b_wins : job.deck_a_wins;
  const l = mineIsB ? job.deck_a_wins : job.deck_b_wins;
  return `<strong>${w}</strong>–${l}${job.draws ? `–${job.draws}` : ""} <span class="hint">(${Math.round((w / done) * 100)}% vinte)</span>`;
}

async function refreshForgeJobs() {
  clearTimeout(forgeTimer);
  const slug = currentDeckSlug;
  let jobs;
  try {
    jobs = await api.get(`forge/simulations?deck=${encodeURIComponent(slug)}&limit=15`);
  } catch (err) {
    document.getElementById("forge-msg").textContent = "Errore: " + forgeError(err);
    return;
  }
  if (slug !== currentDeckSlug) return;
  const tbody = document.querySelector("#forge-jobs tbody");
  tbody.innerHTML = jobs.length
    ? jobs
        .map((j) => {
          const pct = Math.round((j.games_completed / j.games_requested) * 100);
          const progress = FORGE_ACTIVE.has(j.status)
            ? ` <span class="forge-bar"><i style="width:${pct}%"></i></span> ${j.games_completed}/${j.games_requested}`
            : ` <span class="hint">${j.games_completed}/${j.games_requested}</span>`;
          const flags = (j.result?.quality_flags || []).map((f) => FORGE_FLAG_LABEL[f] || esc(f)).join("<br>");
          return `<tr data-id="${esc(j.id)}">
            <td>${j.kind === "audit" ? "🔍 Audit" : "⚔️ Sim"}<br><span class="hint">${esc((j.created_at || "").replace("T", " ").slice(0, 16))}</span></td>
            <td>${esc(forgeOpponent(j, slug))}</td>
            <td><span class="pill ${FORGE_STATUS_PILL[j.status] || ""}">${esc(j.status)}</span>${progress}</td>
            <td>${forgeResult(j, slug)}</td>
            <td class="hint">${flags}</td>
            <td>
              <button class="link" data-act="detail">dettagli</button>
              <a class="link" href="${API}forge/simulations/${encodeURIComponent(j.id)}/log" target="_blank" rel="noopener">log</a>
              ${FORGE_ACTIVE.has(j.status) ? '<button class="link danger" data-act="cancel">annulla</button>' : ""}
            </td>
          </tr>`;
        })
        .join("")
    : `<tr><td colspan="6" class="hint">Nessun test ancora: lancia un audit o una simulazione.</td></tr>`;
  if (forgeOpenJob && jobs.some((j) => j.id === forgeOpenJob)) showForgeDetail(forgeOpenJob);
  const viewActive = document.getElementById("view-deck").classList.contains("active");
  if (viewActive && jobs.some((j) => FORGE_ACTIVE.has(j.status))) forgeTimer = setTimeout(refreshForgeJobs, 4000);
}

async function showForgeDetail(id) {
  const box = document.getElementById("forge-detail");
  forgeOpenJob = id;
  let job;
  try {
    job = await api.get(`forge/simulations/${encodeURIComponent(id)}`);
  } catch (err) {
    box.hidden = false;
    box.textContent = "Errore: " + forgeError(err);
    return;
  }
  const groups = {
    ERROR: "❌ Struttura",
    RED: "⛔ Non supportate da Forge (scartate dal mazzo)",
    YELLOW: "⚠️ AI fallback (giocate con AI semplificata)",
    INFO: "ℹ️ Note di conversione",
  };
  const deckName = (side) => ((job.decks || []).find((d) => d.side === side)?.deck_name || "").replace(/_/g, " ");
  const issueHtml = Object.entries(groups)
    .map(([sev, title]) => {
      const items = (job.issues || []).filter((i) => i.severity === sev);
      if (!items.length) return "";
      return `<strong>${title}</strong> (${items.length})<ul>` +
        items
          .map((i) =>
            `<li>${i.side && job.kind !== "audit" ? `<span class="hint">[${esc(deckName(i.side))}]</span> ` : ""}` +
            (i.card_name
              ? `<strong>${esc(i.card_name)}</strong>` +
                (i.set_code ? ` <span class="hint">${esc(i.set_code)}${i.collector_number ? " #" + esc(i.collector_number) : ""}</span>` : "") +
                " — "
              : "") +
            `${esc(i.message)}</li>`)
          .join("") +
        "</ul>";
    })
    .join("");
  const r = job.result || {};
  box.hidden = false;
  box.innerHTML =
    `<h3>${job.kind === "audit" ? "Audit" : "Simulazione"} ${esc(job.id)}</h3>` +
    `<p class="hint">${esc((job.decks || []).map((d) => d.deck_name).join(" vs "))} · ${esc(job.format)} · ` +
    `Forge ${esc(job.forge_version || "")} / Java ${esc(job.java_version || "")} · ` +
    `sha256 ${esc((job.decks?.[0]?.deck_hash || "").slice(0, 12))}</p>` +
    (job.error ? `<p class="err">${esc(job.error)}</p>` : "") +
    (job.kind === "simulation" && job.games_completed
      ? `<p>${esc(deckName("a"))}: <strong>${job.deck_a_wins}</strong> · ${esc(deckName("b"))}: <strong>${job.deck_b_wins}</strong> · patte: ${job.draws} ` +
        `<span class="hint">— risultati osservati con l'AI di Forge, non una previsione del metagame reale.</span></p>`
      : "") +
    (r.audit
      ? `<p>Struttura <strong>${esc(r.audit.structure)}</strong> · Caricamento Forge <strong>${esc(r.audit.forge_load)}</strong> · <span class="hint">${esc(r.audit.note)}</span></p>`
      : "") +
    (issueHtml || `<p class="ok">Nessun problema rilevato.</p>`) +
    `<button class="btn" id="forge-detail-close">Chiudi</button>`;
  document.getElementById("forge-detail-close").addEventListener("click", () => {
    box.hidden = true;
    forgeOpenJob = null;
  });
}

document.getElementById("forge-scope").addEventListener("change", updateForgeDckLink);

document.getElementById("forge-audit-btn").addEventListener("click", async () => {
  const msg = document.getElementById("forge-msg");
  msg.textContent = "Invio audit…";
  try {
    const r = await api.send("POST", `decks/${currentDeckSlug}/forge/audit`, { scope: document.getElementById("forge-scope").value });
    msg.textContent = `Audit ${r.job_id} in coda.`;
    forgeOpenJob = r.job_id;
    refreshForgeJobs();
  } catch (err) {
    msg.textContent = "Errore: " + forgeError(err);
  }
});

document.getElementById("forge-sim-btn").addEventListener("click", async () => {
  const msg = document.getElementById("forge-msg");
  const opp = document.getElementById("forge-opponent").value;
  if (!opp) { msg.textContent = "Scegli un avversario."; return; }
  const body = {
    deck: currentDeckSlug,
    games: parseInt(document.getElementById("forge-games").value, 10) || 1,
    scope: document.getElementById("forge-scope").value,
    allow_invalid: document.getElementById("forge-allow-invalid").checked,
  };
  if (opp.startsWith("deck:")) body.opponent_deck = opp.slice(5);
  else body.opponent_test_deck = opp.slice(5);
  msg.textContent = "Invio simulazione…";
  try {
    const r = await api.send("POST", "forge/simulations", body);
    msg.textContent = `Simulazione ${r.job_id} in coda` +
      (r.issues?.RED ? ` — ⚠️ ${r.issues.RED} carte non supportate da Forge (vedi dettagli).` : ".");
    refreshForgeJobs();
  } catch (err) {
    msg.textContent = "Errore: " + forgeError(err);
  }
});

document.querySelector("#forge-jobs tbody").addEventListener("click", async (e) => {
  const act = e.target.dataset.act;
  const row = e.target.closest("tr");
  if (!act || !row) return;
  const id = row.dataset.id;
  if (act === "detail") return showForgeDetail(id);
  if (act === "cancel") {
    try {
      await api.send("POST", `forge/simulations/${encodeURIComponent(id)}/cancel`);
    } catch (err) {
      document.getElementById("forge-msg").textContent = "Errore: " + forgeError(err);
    }
    refreshForgeJobs();
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

// ---------- Cleanup: remove out-of-scope cards ----------
document.getElementById("cleanup-btn").addEventListener("click", async () => {
  const out = document.getElementById("cleanup-result");
  if (!confirm("Rimuovere dalla collezione (e dal deck) tutte le carte che non sono Hobbit o LOTR?")) return;
  out.textContent = "Rimozione…";
  try {
    const data = await api.send("POST", "cards/cleanup", null);
    out.textContent = `Rimosse ${data.deleted} carte:\n` + data.cards.map((c) => `- ${c.name} (${c.set})`).join("\n");
    setsCache = [];
  } catch (err) {
    out.textContent = "Errore: " + err.message;
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

async function fetchScryfall(name, withPrice = false) {
  // Thumbnails don't need prices; only the modal asks for them, so the deck
  // view's per-row thumbnail loads stay cheap.
  const key = withPrice ? `${name}|price` : name;
  if (scryfallCache.has(key)) return scryfallCache.get(key);
  // Go through our backend proxy (same origin → no CORS; server caches results
  // and prefers the Middle-earth printing).
  const data = await api.get(
    "scryfall?name=" + encodeURIComponent(name) + (withPrice ? "&with_price=true" : "")
  );
  scryfallCache.set(key, data);
  return data;
}

async function openCardModal(name) {
  const overlay = document.getElementById("card-modal");
  const img = document.getElementById("modal-img");
  const nameEl = document.getElementById("modal-name");
  const typeEl = document.getElementById("modal-type");
  const oracleEl = document.getElementById("modal-oracle");
  const priceEl = document.getElementById("modal-price");
  const scry = document.getElementById("modal-scryfall");
  const cm = document.getElementById("modal-cardmarket");

  nameEl.textContent = name;
  typeEl.textContent = "";
  priceEl.textContent = "";
  oracleEl.textContent = "Caricamento da Scryfall…";
  img.removeAttribute("src");
  scry.href = "https://scryfall.com/search?q=" + encodeURIComponent('!"' + name + '"');
  cm.href = cardmarketUrl(name);
  overlay.hidden = false;

  try {
    const c = await fetchScryfall(name, true);
    if (c.image) img.src = c.image;
    nameEl.textContent = c.name || name;
    const flavour = c.flavor_name ? `🗺️ Middle-earth: “${c.flavor_name}”  •  ` : "";
    typeEl.textContent = flavour + [c.type_line, c.mana_cost].filter(Boolean).join("  •  ");
    oracleEl.textContent = c.oracle_text || "";
    priceEl.textContent =
      c.cardmarket_price_gbp != null
        ? `🛒 Cardmarket ≈ £${c.cardmarket_price_gbp.toFixed(2)} (€${c.cardmarket_price_eur.toFixed(2)})`
        : c.cardmarket_price_eur != null ? `🛒 Cardmarket ≈ €${c.cardmarket_price_eur.toFixed(2)}` : "";
    if (c.scryfall_uri) scry.href = c.scryfall_uri;
    if (c.cardmarket) cm.href = c.cardmarket;
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
