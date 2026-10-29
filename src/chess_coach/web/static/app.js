// chess-coach web: plain JS, no libraries. Text always goes through textContent; only the board
// SVGs the server draws with python-chess are inserted as markup.

const $ = (sel) => document.querySelector(sel);

function el(tag, attrs = {}, ...children) {
  const node = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (k === "class") node.className = v;
    else if (k === "onclick") node.addEventListener("click", v);
    else node.setAttribute(k, v);
  }
  for (const c of children.flat()) {
    if (c === null || c === undefined || c === false) continue;
    node.append(c instanceof Node ? c : document.createTextNode(String(c)));
  }
  return node;
}

function fill(node, ...children) {
  // replaceChildren() does not flatten arrays; this does, and skips empty parts like el().
  node.replaceChildren(...children.flat(Infinity).filter((c) => c !== null && c !== undefined && c !== false)
    .map((c) => (c instanceof Node ? c : document.createTextNode(String(c)))));
}

function boardNode(svg, cls = "board") {
  const div = el("div", { class: cls });
  div.innerHTML = svg; // drawn by the server from a FEN, never from user text
  return div;
}

async function api(path, options) {
  const r = await fetch(path, options);
  if (!r.ok) throw new Error((await r.json().catch(() => ({}))).detail || r.statusText);
  return r.json();
}

// -- tabs ----------------------------------------------------------------------------------
const loaded = {};
function show(tab, arg) {
  for (const b of document.querySelectorAll("nav button")) b.classList.toggle("on", b.dataset.tab === tab);
  for (const s of document.querySelectorAll("main section")) s.hidden = s.id !== tab;
  if (tab === "practice") return openPractice(arg);
  if (!loaded[tab]) loaded[tab] = { mistakes: loadMistakes, openings: loadOpenings, progress: loadProgress }[tab]();
}
for (const b of document.querySelectorAll("nav button")) b.addEventListener("click", () => show(b.dataset.tab));

// -- what you get wrong --------------------------------------------------------------------
function example(e) {
  return el(
    "div", { class: "example" },
    boardNode(e.svg),
    el("div", {}, "You played ", el("span", { class: "played" }, e.move),
      " · better ", el("span", { class: "better" }, e.best),
      el("span", { class: "muted small" }, ` · win chance −${e.loss}%`)),
    e.url ? el("a", { href: e.url, target: "_blank", rel: "noopener" }, "open the game") : null,
  );
}

function habitCard(h, tags) {
  const canPractise = h.practice && tags.has(h.practice);
  return el(
    "div", { class: "card" },
    el("h3", {}, h.title),
    el("div", { class: "muted" }, h.detail),
    h.lesson ? el("div", { class: "lesson" }, el("b", {}, "What to learn: "), h.lesson) : null,
    el("div", { class: "examples" }, h.examples.map(example)),
    canPractise ? el("p", {}, el("button", { class: "primary", onclick: () => show("practice", h.practice) },
      "Practise these positions")) : null,
  );
}

async function loadMistakes() {
  const [o, tagList] = await Promise.all([api("/api/overview"), api("/api/drill/tags")]);
  const tags = new Set(tagList.map((t) => t.tag));
  $("#summary").textContent =
    `${o.games} analysed games (${o.first} to ${o.last}). You make a mistake or blunder on ` +
    `${o.error_rate}% of your moves (average centipawn loss ${o.acpl}); you lost ${o.losses} of ${o.decided}.` +
    (o.rushed_moves ? ` ${o.rushed_moves} moves played short on time (under 15 s in bullet) are left out, unless the same mistake keeps repeating.` : "");
  const root = $("#mistakes");
  fill(root,
    el("h2", {}, "Mistakes you repeat"),
    el("p", { class: "muted" }, "Counted from Stockfish's verdict on your own moves. Red is what you played, green what to play."),
    o.habits.length ? o.habits.map((h) => habitCard(h, tags)) : el("p", {}, "Nothing stands out yet."),
    el("h2", {}, "When it happens"),
    el("p", { class: "muted" }, "Situations where your mistake rate is clearly above your own average."),
    o.situations.length ? o.situations.map((h) => habitCard(h, tags)) : el("p", {}, "No situation stands out yet: analyse more games."),
    o.pressure.length ? [
      el("h2", {}, "Short on time, but it keeps happening"),
      el("p", { class: "muted" }, "Fast moves are reflexes, so they only count here when the same mistake shows up again and again (15+ times across 8+ games)."),
      o.pressure.map((h) => habitCard(h, tags)),
    ] : null,
  );
}

// -- practice ------------------------------------------------------------------------------
let current = null;
let from = null;

function squareAt(svg, evt, side) {
  const pt = svg.createSVGPoint();
  pt.x = evt.clientX;
  pt.y = evt.clientY;
  const p = pt.matrixTransform(svg.getScreenCTM().inverse());
  const vb = svg.viewBox.baseVal;
  const margin = (vb.width - 360) / 2;
  let file = Math.floor((p.x - margin) / 45);
  let rank = 7 - Math.floor((p.y - margin) / 45);
  if (file < 0 || file > 7 || rank < 0 || rank > 7) return null;
  if (side === "black") { file = 7 - file; rank = 7 - rank; }
  return { name: "abcdefgh"[file] + (rank + 1), x: file, y: rank };
}

function mark(svg, sq, side) {
  svg.querySelector(".pick")?.remove();
  if (!sq) return;
  const vb = svg.viewBox.baseVal;
  const margin = (vb.width - 360) / 2;
  const fx = side === "black" ? 7 - sq.x : sq.x;
  const fy = side === "black" ? sq.y : 7 - sq.y;
  const r = document.createElementNS("http://www.w3.org/2000/svg", "rect");
  r.setAttribute("class", "pick");
  r.setAttribute("x", margin + fx * 45);
  r.setAttribute("y", margin + fy * 45);
  r.setAttribute("width", 45);
  r.setAttribute("height", 45);
  r.setAttribute("fill", "rgba(42,157,143,.35)");
  r.style.pointerEvents = "none";
  svg.append(r);
}

async function openPractice(tag) {
  const tags = await api("/api/drill/tags");
  const select = $("#tag");
  const keep = tag ?? select.value;
  fill(select, el("option", { value: "" }, "everything due"),
    tags.map((t) => el("option", { value: t.tag }, `${t.tag.replaceAll("_", " ")} (${t.due} due of ${t.puzzles})`)));
  select.value = keep || "";
  await nextPuzzle();
}
$("#tag").addEventListener("change", nextPuzzle);

async function nextPuzzle() {
  from = null;
  $("#feedback").textContent = "";
  $("#next").hidden = true;
  $("#giveup").hidden = false;
  const tag = $("#tag").value;
  const p = await api("/api/drill/next" + (tag ? `?tag=${encodeURIComponent(tag)}` : ""));
  current = p.done ? null : p;
  if (p.done) {
    fill($("#board"));
    $("#prompt").textContent = p.puzzles
      ? "Nothing due here today. Solved puzzles come back later; missed ones tomorrow."
      : "No puzzles yet. Run `chess-coach puzzles make` (and `--openings`).";
    $("#due").textContent = "";
    $("#giveup").hidden = true;
    return;
  }
  $("#due").textContent = `${p.due} due`;
  fill($("#board"), boardNode(p.svg, "board"));
  fill($("#prompt"),
    p.opening_fix
      ? el("span", {}, `Your opening (${p.tags[1] || ""}): here you usually play `, el("span", { class: "played" }, p.played), ". Find a better move.")
      : el("span", {}, "In the game you played ", el("span", { class: "played" }, p.played), ". Find the best move."),
    el("br"), el("span", { class: "muted small" }, `${p.side === "white" ? "White" : "Black"} to move · ${p.tags.join(", ").replaceAll("_", " ")}`),
  );
  const svg = $("#board svg");
  svg.addEventListener("click", (evt) => {
    if (!current) return;
    const sq = squareAt(svg, evt, current.side);
    if (!sq) return;
    if (!from) { from = sq; mark(svg, sq, current.side); return; }
    if (sq.name === from.name) { from = null; mark(svg, null); return; }
    answer(from.name + sq.name);
  });
}

async function answer(uci) {
  if (!current) return;
  let r;
  try {
    r = await api("/api/drill/answer", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ id: current.id, uci }),
    });
  } catch (err) {
    from = null;
    mark($("#board svg"), null);
    $("#feedback").textContent = "That isn't a legal move here. Try again.";
    return;
  }
  const game = current.game;
  current = null;
  fill($("#board"), boardNode(r.svg, "board"));
  fill($("#feedback"),
    r.correct ? el("b", { class: "better" }, "Correct. ") : el("b", { class: "played" }, uci === "?" ? "" : `${r.answer} isn't it. `),
    "The move: ", el("span", { class: "better" }, r.solution),
    r.also.length ? ` (also good: ${r.also.join(", ")})` : "",
    el("br"), el("span", { class: "muted small" }, `Line: ${r.line} · next time ${r.next_due}`),
    game ? el("span", {}, " · ", el("a", { href: game, target: "_blank", rel: "noopener" }, "the game")) : null,
  );
  $("#giveup").hidden = true;
  $("#next").hidden = false;
}
$("#giveup").addEventListener("click", () => answer("?"));
$("#next").addEventListener("click", nextPuzzle);

// -- openings ------------------------------------------------------------------------------
function favoriteCard(f) {
  const pct = (n) => `${(100 * n) / f.games}%`;
  return el(
    "div", { class: "card" },
    el("h3", {}, f.family),
    el("div", { class: "muted" }, `${f.games} games · scored ${f.score}% (${f.wins} won, ${f.draws} drawn, ${f.losses} lost) · mistakes on ${f.opening_error_rate}% of your opening moves`),
    el("div", { class: "score" }, el("div", { class: "w", style: `width:${pct(f.wins)}` }),
      el("div", { class: "d", style: `width:${pct(f.draws)}` }), el("div", { class: "l", style: `width:${pct(f.losses)}` })),
    f.line ? el("p", {}, "Your usual line: ", el("code", {}, f.line)) : null,
    f.book ? el("p", { class: "muted small" }, `Book (${f.names[0]?.name}): `, el("code", {}, f.book)) : null,
    f.fixes.length
      ? el("div", {},
        el("div", { class: "lesson" }, el("b", {}, "Improve it: "), "positions you reached in several games where your usual move loses ground."),
        el("div", { class: "examples" }, f.fixes.map((x) => el("div", { class: "example" },
          boardNode(x.svg),
          el("div", {}, "You play ", el("span", { class: "played" }, x.move), ` (${x.times} games, −${x.loss_cp} cp on average) · better `,
            el("span", { class: "better" }, x.best)),
          x.urls[0] ? el("a", { href: x.urls[0], target: "_blank", rel: "noopener" }, "an example") : null))),
        el("p", {}, el("button", { class: "primary", onclick: () => show("practice", f.family) }, "Drill these fixes")))
      : el("p", { class: "muted small" }, "No repeated weak move here: your play in this opening holds up."),
  );
}

async function loadOpenings() {
  const o = await api("/api/openings");
  fill($("#openings"),
    el("p", { class: "muted" }, "Your most-played openings, how you score, and where your usual move can be improved (named book moves are never flagged)."),
    el("div", { class: "columns" },
      el("div", {}, el("h2", {}, "With White"), o.white.length ? o.white.map(favoriteCard) : el("p", {}, "Not enough games yet.")),
      el("div", {}, el("h2", {}, "With Black"), o.black.length ? o.black.map(favoriteCard) : el("p", {}, "Not enough games yet."))),
  );
}

// -- progress ------------------------------------------------------------------------------
async function loadProgress() {
  const p = await api("/api/progress");
  const rows = p.periods.map((r) => el("tr", {}, el("td", {}, r.label), el("td", {}, r.games), el("td", {}, `${r.score}%`),
    el("td", {}, `${r.error_rate}%`), el("td", {}, r.acpl)));
  fill($("#progress"),
    el("h2", {}, "Your games by month"),
    el("table", {}, el("tr", {}, ["month", "games", "score", "mistakes + blunders", "average cp loss"].map((h) => el("th", {}, h))), rows),
    el("h2", {}, "Puzzles"),
    p.puzzles.attempts
      ? el("div", {},
        el("p", {}, `Solved ${p.puzzles.solved} of ${p.puzzles.attempts} attempts.`),
        el("table", {}, el("tr", {}, ["theme", "attempts", "solved"].map((h) => el("th", {}, h))),
          p.tags.map((t) => el("tr", {}, el("td", {}, t.tag.replaceAll("_", " ")), el("td", {}, t.attempts), el("td", {}, `${t.rate}%`)))))
      : el("p", { class: "muted" }, "No attempts yet: try the Practice tab."),
  );
}

show("mistakes");
