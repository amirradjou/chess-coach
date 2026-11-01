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

function reasons(list) {
  // Chess reasons worked out from the board (coach/principles.py), not by a model.
  return list?.length ? el("ul", { class: "reasons" }, list.map((r) => el("li", {}, r))) : null;
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
  if (tab === "coach") return openCoach();
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
    reasons(e.reasons),
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

// The same mistake from the very same position in 3+ games, with their best answer to it.
function repeatCard(r) {
  const drill = r.puzzle ? "it has a puzzle" : "no puzzle for it yet";
  return el(
    "div", { class: "example" },
    boardNode(r.svg),
    el("div", {}, "You played ", el("span", { class: "played" }, r.move), ` in ${r.games} games`,
      r.family ? ` (${r.family})` : "", " · better ", el("span", { class: "better" }, r.better)),
    r.punish ? el("div", { class: "small" }, "Their best answer: ", el("b", {}, r.punish),
      ` (they found it ${r.punished} of ${r.replies} times)`) : null,
    reasons(r.reasons),
    // Games imported from a PGN without a date have none (last: null)
    el("div", { class: "muted small" }, r.last ? `Last on ${r.last} · ${drill}` : drill[0].toUpperCase() + drill.slice(1)),
    el("div", { class: "small" }, r.urls.flatMap((url, i) => [i ? " · " : "",
      el("a", { href: url, target: "_blank", rel: "noopener" }, `game ${i + 1}`)])),
  );
}

async function loadMistakes() {
  const [o, tagList, repeated] = await Promise.all([api("/api/overview"), api("/api/drill/tags"), api("/api/repeats")]);
  const tags = new Set(tagList.map((t) => t.tag));
  $("#summary").textContent =
    `${o.games} analysed games (${o.first} to ${o.last}). You make a mistake or blunder on ` +
    `${o.error_rate}% of your moves (average centipawn loss ${o.acpl}); you lost ${o.losses} of ${o.decided}${o.bullet_games ? " outside bullet" : ""}.` +
    (o.bullet_games ? ` In your ${o.bullet_games} bullet games only the first 10 moves count: after that the clock decides, so later moves and results are left out.` : "") +
    (o.rushed_moves ? ` ${o.rushed_moves} moves played short on time (under a tenth of the clock) are left out, unless the same mistake keeps repeating.` : "");
  const root = $("#mistakes");
  fill(root,
    el("h2", {}, "Mistakes you repeat"),
    el("p", { class: "muted" }, "Counted from Stockfish's verdict on your own moves. Red is what you played, green what to play."),
    o.habits.length ? o.habits.map((h) => habitCard(h, tags)) : el("p", {}, "Nothing stands out yet."),
    repeated.length ? [
      el("h2", {}, "The same mistake in the same position"),
      el("p", { class: "muted" }, "Your inaccuracies or worse from the very same position in 3 or more games (named book moves left out). Red is what you played, green the better move."),
      el("div", { class: "card" }, el("div", { class: "examples" }, repeated.map(repeatCard))),
    ] : null,
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
// While solving, a move from the puzzle position is an answer. A wrong one shows their best reply
// and what it costs, and you can try again (only the first answer counts for the schedule). Once
// it's solved or shown, the lines can be stepped through and any move you play on the board is
// answered by the engine.
const COLOR = { best: "best", tried: "played", game: "played", analysis: "tried" };
const boards = new Map(); // board URL -> SVG text, for this puzzle
let drill = null;
let drawn = 0;

function fresh(puzzle) {
  return { puzzle, solving: true, tries: [], result: null, lines: [], line: 0, ply: 0, from: null };
}

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

function buttons({ retry = false, giveup = false, next = false }) {
  $("#retry").hidden = !retry;
  $("#giveup").hidden = !giveup;
  $("#next").hidden = !next;
}

async function nextPuzzle() {
  boards.clear();
  drill = null;
  fill($("#feedback"));
  fill($("#lines"));
  $("#steps").hidden = true;
  buttons({});
  const tag = $("#tag").value;
  const p = await api("/api/drill/next" + (tag ? `?tag=${encodeURIComponent(tag)}` : ""));
  if (p.done) {
    fill($("#board"));
    $("#prompt").textContent = p.puzzles
      ? "Nothing due here today. Solved puzzles come back later; missed ones tomorrow."
      : "No puzzles yet. Run `chess-coach puzzles make` (and `--openings`).";
    $("#due").textContent = "";
    return;
  }
  drill = fresh(p);
  $("#due").textContent = `${p.due} due`;
  fill($("#prompt"),
    p.opening_fix
      ? el("span", {}, `Your opening (${p.opening || ""}): here you usually play `, el("span", { class: "played" }, p.played), ". Find a better move.")
      : el("span", {}, "In the game you played ", el("span", { class: "played" }, p.played), ". Find the best move."),
    el("br"), el("span", { class: "muted small" }, `${p.side === "white" ? "White" : "Black"} to move · ${p.tags.join(", ").replaceAll("_", " ")}`),
    p.opening_idea ? el("span", { class: "idea" }, el("b", {}, `${p.opening}: `), p.opening_idea) : null,
  );
  buttons({ giveup: true });
  await draw();
}

// The position on show: the puzzle itself (ply 0) or the position after `ply` moves of a line,
// with the line's next move as an arrow.
function shown() {
  const line = drill.lines[drill.line];
  const before = drill.ply > 0 ? line.plies[drill.ply - 1] : null;
  return {
    fen: before ? before.fen : drill.puzzle.fen,
    last: before?.uci,
    next: line?.plies[drill.ply]?.uci,
    color: COLOR[line?.kind] || "best",
  };
}

async function boardSvg({ fen, last, next, color }, orientation) {
  const q = new URLSearchParams({ fen, orientation, color });
  if (last) q.set("last", last);
  if (next) q.set("arrow", next);
  const url = `/api/board?${q}`;
  if (!boards.has(url)) {
    const r = await fetch(url);
    if (!r.ok) throw new Error(r.statusText);
    boards.set(url, await r.text());
  }
  return boards.get(url);
}

async function draw() {
  const token = ++drawn;
  const svg = await boardSvg(shown(), drill.puzzle.side);
  if (token !== drawn || !drill) return; // a later step or puzzle took over
  drill.from = null;
  fill($("#board"), boardNode(svg, "board"));
  $("#board svg").addEventListener("click", onBoard);
  drawLines();
  drawSteps();
}

function drawSteps() {
  const line = drill.lines[drill.line];
  $("#steps").hidden = !drill.lines.length;
  if (!line) return;
  const ply = drill.ply > 0 ? line.plies[drill.ply - 1] : null;
  $("#where").textContent = ply ? `${ply.number}${ply.san}${ply.engine ? " (engine)" : ""}` : "start";
}

function step(to) {
  const n = drill?.lines[drill.line]?.plies.length;
  if (n === undefined) return;
  drill.ply = { first: 0, prev: Math.max(0, drill.ply - 1), next: Math.min(n, drill.ply + 1), last: n }[to];
  draw();
}
for (const b of document.querySelectorAll("#steps button")) b.addEventListener("click", () => step(b.dataset.step));
document.addEventListener("keydown", (e) => {
  const keys = { ArrowLeft: "prev", ArrowRight: "next", Home: "first", End: "last" };
  if ($("#practice").hidden || !drill?.lines.length || !keys[e.key] || e.target.closest("select, input, textarea")) return;
  e.preventDefault();
  step(keys[e.key]);
});

function drawLines() {
  fill($("#lines"), drill.lines.map((line, li) => el(
    "div", { class: `line ${line.kind}${li === drill.line ? " on" : ""}` },
    el("div", { class: "line-head" },
      el("button", { class: `link ${line.kind === "best" ? "better" : line.kind === "analysis" ? "" : "played"}`, onclick: () => { drill.line = li; drill.ply = 0; draw(); } }, line.label),
      line.score ? el("span", { class: "eval" }, line.score) : null),
    el("div", { class: "moves" }, line.plies.map((p, i) => el(
      "button",
      { class: `chip${p.engine ? " engine" : ""}${li === drill.line && i + 1 === drill.ply ? " on" : ""}`, onclick: () => { drill.line = li; drill.ply = i + 1; draw(); } },
      i === 0 || !p.number.endsWith("...") ? `${p.number}${p.san}` : p.san))),
    line.why && li === drill.line ? el("div", { class: "why" }, line.why) : null,
    li === drill.line ? reasons([...(line.faults || []), ...(line.ideas || [])]) : null,
  )));
}

async function onBoard(evt) {
  const svg = evt.currentTarget;
  const sq = squareAt(svg, evt, drill.puzzle.side);
  if (!sq) return;
  if (!drill.from) { drill.from = sq; mark(svg, sq, drill.puzzle.side); return; }
  const uci = drill.from.name + sq.name;
  drill.from = null;
  mark(svg, null);
  if (uci.slice(0, 2) === uci.slice(2)) return;
  if (!drill.solving) return explore(uci);
  if (drill.ply !== 0) {
    // While solving, answers are moves from the puzzle position.
    drill.ply = 0;
    await draw();
    $("#where").textContent = "start: try your move here";
    return;
  }
  answer(uci);
}

async function answer(uci) {
  const first = drill.tries.length === 0 && !drill.result;
  let r;
  try {
    r = await api("/api/drill/answer", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ id: drill.puzzle.id, uci, record: first }),
    });
  } catch (err) {
    fill($("#feedback"), "That isn't a legal move here. Try again.");
    return;
  }
  if (first) drill.due = r.next_due;
  drill.result = r;
  if (r.correct) return reveal(r.uci);
  if (r.answer === null) return reveal(null); // gave up
  drill.tries.push(r.uci);
  const at = drill.lines.findIndex((l) => l.plies[0]?.uci === r.uci);
  if (at >= 0) drill.lines[at] = r.tried;
  else drill.lines.push(r.tried);
  drill.line = at >= 0 ? at : drill.lines.length - 1;
  drill.ply = r.tried.plies.length > 1 ? 1 : 0; // after your move, with their reply as the arrow
  fill($("#feedback"),
    el("b", { class: "played" }, `${r.answer} isn't it.`), " Their answer and what it costs are below.",
    el("br"), el("span", { class: "muted small" },
      `Try again from the start, or show the answer. This counts as a miss (back on ${drill.due}).`));
  buttons({ retry: true, giveup: true });
  await draw();
}

async function reveal(found) {
  drill.solving = false;
  buttons({});
  const tried = found ? [...drill.tries, found] : drill.tries;
  const q = new URLSearchParams({ id: drill.puzzle.id });
  for (const t of tried) q.append("tried", t);
  fill($("#feedback"), el("span", { class: "muted" }, "Working out the lines…"));
  const { lines } = await api(`/api/drill/review?${q}`);
  if (!drill) return;
  drill.lines = lines;
  drill.line = 0;
  drill.ply = 0;
  const r = drill.result;
  const game = drill.puzzle.game;
  fill($("#feedback"),
    found
      ? el("b", { class: "better" }, drill.tries.length ? `Found it on try ${drill.tries.length + 1}. ` : "Correct. ")
      : el("span", {}, "The answer: ", el("b", { class: "better" }, r.solution), ". "),
    found && r.answer !== r.solution ? ["The engine's first choice: ", el("b", { class: "better" }, r.solution), ". "] : null,
    r.also.length ? `Also good: ${r.also.join(", ")}. ` : null,
    el("br"),
    el("span", { class: "muted small" },
      `Next time: ${drill.due}. Play any move on the board to see the engine's answer.`),
    game ? el("span", { class: "small" }, " ", el("a", { href: game, target: "_blank", rel: "noopener" }, "Open the game")) : null,
  );
  buttons({ next: true });
  await draw();
}

async function explore(uci) {
  const line = drill.lines[drill.line];
  const before = line ? line.plies.slice(0, drill.ply).map((p) => p.uci) : [];
  let a;
  try {
    a = await api("/api/explore", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ fen: drill.puzzle.fen, moves: [...before, uci] }),
    });
  } catch (err) {
    $("#where").textContent = "that isn't a legal move here";
    return;
  }
  const at = drill.lines.findIndex((l) => l.kind === "analysis");
  if (at >= 0) drill.lines[at] = a;
  else drill.lines.push(a);
  drill.line = at >= 0 ? at : drill.lines.length - 1;
  drill.ply = before.length + 1;
  await draw();
}

$("#retry").addEventListener("click", () => { drill.ply = 0; draw(); });
$("#giveup").addEventListener("click", () => (drill.result ? reveal(null) : answer("?")));
$("#next").addEventListener("click", nextPuzzle);

// -- learn openings ------------------------------------------------------------------------
// Your openings as a tree of the moves played in your games. Explore it; train your side (they
// play the replies you meet, as often as you meet them; the engine takes over where your games
// stop); or work through the mistakes your opponents make, finding the punishment for each.
const LINE_END = 20; // plies of training before a line counts as done
let coach = null;

const games = (n) => `${n} game${n === 1 ? "" : "s"}`;
// Bullet results don't count, so a score may come from fewer games than reached it, or none.
const scored = (x, sep = " · ", words = "you scored") => (x.score === null ? ""
  : `${sep}${words} ${x.score}%${x.scored < x.games ? ` in the ${games(x.scored)} outside bullet` : ""}`);
const a = (word) => `${/^[aeiou]/.test(word) ? "an" : "a"} ${word}`;
// The engine's eval after your 10th move in the games through here (the median; not results).
const afterTen = (x, long = false) => (!x.after_ten ? ""
  : long ? ` · engine eval after your 10th move: ${x.after_ten.median} (median of ${games(x.after_ten.games)}; not results)`
    : ` · ${x.after_ten.median} after move 10`);
const optionLabel = (x) => `${x.family} (${games(x.games)}${scored(x, ", ", "you score")}${x.due ? `, ${x.due} due` : ""})`;

async function openCoach() {
  if (coach) return;
  const o = await api("/api/coach/openings");
  fill($("#coach-opening"), [["white", "With White"], ["black", "With Black"]].map(([color, label]) => (o[color].length
    ? el("optgroup", { label }, o[color].map((x) => el("option", { value: `${color}|${x.family}` }, optionLabel(x))))
    : null)));
  coach = { openings: o, color: null, family: null, mode: "explore", path: [], node: null, from: null, check: null, reveal: false, deck: null, card: 0, pending: null, cards: null, answered: false };
  await pickOpening();
}
$("#coach-opening").addEventListener("change", pickOpening);

async function pickOpening() {
  const value = $("#coach-opening").value;
  if (!value) return fill($("#coach-title"), "No opening with 5+ analysed games yet.");
  const [color, family] = [value.slice(0, value.indexOf("|")), value.slice(value.indexOf("|") + 1)];
  Object.assign(coach, { color, family, deck: null, card: 0 });
  const info = coach.openings[color].find((x) => x.family === family);
  fill($("#coach-idea"), info?.idea ? el("div", { class: "idea" }, el("b", {}, `${family}: `), info.idea) : null);
  await loadCards();
  await coachStart();
}

// -- remembering your practice: each position you answer is a card with an SM-2 schedule -----
async function loadCards() {
  coach.cards = await api(`/api/coach/cards?${new URLSearchParams({ family: coach.family, color: coach.color })}`);
  showCards();
}

function showCards() {
  const c = coach.cards;
  const info = coach.openings[coach.color].find((x) => x.family === coach.family);
  if (info) {
    info.due = c.due;
    const option = [...$("#coach-opening").options].find((o) => o.value === `${coach.color}|${coach.family}`);
    if (option) option.textContent = optionLabel(info);
  }
  const parts = [
    c.total ? `You've found ${c.found} of ${c.total} punishments` : null,
    c.lines ? `practised ${c.lines} position${c.lines === 1 ? "" : "s"} on your lines` : null,
  ].filter(Boolean);
  fill($("#coach-progress"), parts.length || c.due
    ? `${parts.join(" · ")}${parts.length ? " · " : ""}${c.due ? `${c.due} due today, and they come first` : "nothing due today"}.`
    : null);
}

// Only your first answer at a position counts (asking to see the move is a miss), as with puzzles.
async function record(uci, correct) {
  if (coach.mode === "explore" || coach.answered) return;
  coach.answered = true;
  try {
    coach.cards = await api("/api/coach/answer", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ family: coach.family, color: coach.color, path: coach.path, uci, correct }),
    });
    showCards();
  } catch (err) {
    fill($("#coach-progress"), `Couldn't save that answer: ${err.message}`);
  }
}

// Due positions come first: in training, their replies steer towards one when your games allow.
function towardDue(moves) {
  const at = coach.path.length;
  const due = (coach.cards?.due_paths ?? []).filter((p) => p.length > at && coach.path.every((u, i) => p[i] === u));
  const ahead = moves.filter((m) => due.some((p) => p[at] === m.uci));
  return ahead.length ? ahead : moves;
}

for (const b of document.querySelectorAll("#coach .modes button")) {
  b.addEventListener("click", () => {
    for (const o of document.querySelectorAll("#coach .modes button")) o.classList.toggle("on", o === b);
    coach.mode = b.dataset.mode;
    coachStart();
  });
}

function coachStart() {
  coach.pending = null;
  return coach.mode === "punish" ? nextMistake() : coachGo([]);
}

// Load the position after `path`; the path and node change together, and the board ignores
// clicks until they have, so a move is never judged against the previous position.
async function coachGo(path, note) {
  const q = new URLSearchParams({ family: coach.family, color: coach.color, path: path.join(",") });
  coach.loading = true;
  let node;
  try {
    node = await api(`/api/coach/node?${q}`);
  } finally {
    coach.loading = false;
  }
  coach.path = [...path];
  coach.node = node;
  coach.reveal = false;
  coach.check = null;
  coach.answered = false;
  await coachDraw();
  fill($("#coach-feedback"), note ?? null);
  const n = coach.node;
  $("#coach-back").hidden = coach.mode === "punish";
  $("#coach-show").hidden = coach.mode === "explore" || n.turn !== "you";
  if (coach.mode === "play" && n.turn === "them") {
    const at = coach.path.length;
    setTimeout(() => { if (coach.node === n && coach.path.length === at) theyMove(); }, 700);
  }
}

async function coachDraw() {
  const n = coach.node;
  const arrow = coach.mode === "explore" ? n.best?.uci : coach.reveal ? n.target?.uci : null;
  const svg = await boardSvg({ fen: n.fen, last: n.path.at(-1)?.uci, next: arrow, color: "best" }, coach.color);
  coach.from = null;
  fill($("#coach-board"), boardNode(svg, "board"));
  $("#coach-board svg").addEventListener("click", onCoachBoard);
  fill($("#coach-path"), n.path.length
    ? n.path.map((p, i) => el("button", { class: "chip", onclick: () => coachJump(i + 1) }, i === 0 || !p.number.endsWith("...") ? `${p.number}${p.san}` : p.san))
    : el("span", { class: "muted small" }, "start position"));
  const who = n.turn === "you" ? "Your move" : "Their move";
  fill($("#coach-title"),
    el("b", {}, n.name || coach.family), el("br"),
    el("span", { class: "muted small" }, n.games
      ? `${who} · reached in ${n.games} of your games${scored(n, " · ", "from here you scored")}${afterTen(n, true)}`
      : `${who} · none of your games reached this position`),
    coach.mode === "explore" && n.best ? el("div", {}, n.turn === "you" ? "Engine's move: " : "Their best: ", el("b", { class: "better" }, n.best.san), ` (${n.best.score} for you)`) : null);
  fill($("#coach-moves"), coach.mode === "explore" ? coachMoves(n) : null);
}

function coachJump(plies) {
  if (coach.mode !== "explore") return;
  coachGo(coach.path.slice(0, plies));
}

function answered(m) {
  if (!m.answers?.length) return "You haven't met it yet.";
  const found = m.answers.filter((a) => a.right).reduce((n, a) => n + a.games, 0);
  const total = m.answers.reduce((n, a) => n + a.games, 0);
  return `You answered ${m.answers.map((a) => `${a.san} ×${a.games}`).join(", ")}: the punishment ${found} of ${total} time${total === 1 ? "" : "s"}.`;
}

function coachMoves(n) {
  if (!n.moves.length) return el("p", { class: "muted" }, "Your games don't go further from here. Play a move on the board to keep exploring with the engine.");
  return el("div", {},
    el("h3", {}, n.turn === "you" ? "What you played here" : "What they played here"),
    n.moves.map((m) => el("div", { class: "move-row" },
      el("div", { class: "line-head" },
        el("span", {},
          el("button", { class: "link", onclick: () => coachGo([...coach.path, m.uci]) }, `${m.number}${m.san}`),
          el("span", { class: "muted small" }, ` · ${games(m.games)}${scored(m)}${afterTen(m)}`)),
        el("span", { class: `verdict ${m.verdict}` }, m.verdict)),
      m.punish ? el("div", { class: "small" }, "Punish it with ", el("b", { class: "better" }, m.punish.san),
        ` (${m.punish.score} for you). ${answered(m)}`) : null,
      reasons(m.reasons))));
}

function weighted(list) {
  let r = Math.random() * list.reduce((n, m) => n + m.games, 0);
  for (const m of list) if ((r -= m.games) <= 0) return m;
  return list.at(-1);
}

function theyMove() {
  const n = coach.node;
  if (coach.path.length >= LINE_END) return fill($("#coach-feedback"), "End of the line: well played. ", restartButton());
  if (n.moves.length) {
    const m = weighted(towardDue(n.moves));
    coach.pending = m.punish ? m : null;
    return coachGo([...coach.path, m.uci], m.punish
      ? [el("b", { class: "played" }, `They play ${m.number}${m.san}`), `, ${a(m.verdict)} (${m.games} of your games). Punish it.`]
      : [el("b", {}, `They play ${m.number}${m.san}`), ` (${m.games} of your games). Your move.`]);
  }
  if (!n.best) return fill($("#coach-feedback"), "End of the line. ", restartButton());
  coach.pending = null;
  coachGo([...coach.path, n.best.uci], [el("b", {}, `They play ${n.best.san}`), " (the engine's move: your games stop here). Your move."]);
}

async function nextMistake() {
  if (coach.deck && coach.card >= coach.deck.length) Object.assign(coach, { deck: null, card: 0 }); // a full pass: due ones first again
  if (!coach.deck) coach.deck = await api(`/api/coach/punish?${new URLSearchParams({ family: coach.family, color: coach.color })}`);
  if (!coach.deck.length) {
    fill($("#coach-board"));
    fill($("#coach-moves"));
    fill($("#coach-title"), "No opponent mistakes seen twice in this opening yet.");
    return fill($("#coach-feedback"));
  }
  const m = coach.deck[coach.card++];
  coach.pending = m;
  const when = m.state === "due" ? "due today" : m.state === "new" ? "new" : `${m.found ? "found" : "missed"} last time, due ${m.due}`;
  await coachGo(m.path, [el("b", { class: "played" }, `They play ${m.number}${m.san}`),
    `, ${a(m.verdict)} you've met in ${games(m.games)}. Punish it. (${coach.card} of ${coach.deck.length}: ${when})`]);
}

function restartButton() {
  return el("button", { onclick: coachStart }, coach.mode === "punish" ? "Next mistake" : "Start again");
}

async function onCoachBoard(evt) {
  if (coach.loading) return;
  const svg = evt.currentTarget;
  const sq = squareAt(svg, evt, coach.color);
  if (!sq) return;
  if (!coach.from) { coach.from = sq; mark(svg, sq, coach.color); return; }
  const uci = coach.from.name + sq.name;
  coach.from = null;
  mark(svg, null);
  if (uci.slice(0, 2) === uci.slice(2)) return;
  if (coach.mode === "explore") {
    try { await coachGo([...coach.path, uci]); } catch (err) { fill($("#coach-feedback"), "That isn't a legal move here."); }
    return;
  }
  if (coach.node.turn !== "you") return;
  const usual = coach.mode === "play" && coach.node.moves.find((m) => m.uci === uci && (m.verdict === "best" || m.verdict === "good"));
  if (usual) {
    record(uci, true);
    return coachGo([...coach.path, uci], [el("b", { class: "better" }, `${usual.san}: good.`),
      ` Your usual move here (${games(usual.games)}${scored(usual, ", ")}), and a sound one.`]);
  }
  let r;
  try {
    r = await api("/api/coach/check", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ fen: coach.node.fen, uci, best: coach.node.best?.uci }),
    });
  } catch (err) {
    return fill($("#coach-feedback"), "That isn't a legal move here.");
  }
  coach.check = r;
  record(r.uci, r.ok);
  if (!r.ok) {
    return fill($("#coach-feedback"),
      el("b", { class: "played" }, `${r.san} isn't it`),
      r.loss !== null ? `: it gives away ${r.loss} against the best move.` : ".",
      reasons(r.faults), el("div", {}, "Try again, or show the move."));
  }
  const met = coach.pending;
  coach.pending = null;
  const praise = [el("b", { class: "better" }, `${r.san}: good.`),
    r.best && r.best.uci !== r.uci ? ` The engine's choice is ${r.best.san}; yours is within ${r.loss}.` : " That's the engine's move.",
    r.score ? ` ${r.score} for you.` : "",
    met ? el("div", { class: "small" }, answered(met)) : null];
  await coachGo([...coach.path, r.uci], coach.mode === "punish" ? [...praise, el("div", {}, restartButton())] : praise);
}

$("#coach-show").addEventListener("click", async () => {
  record("?", false);
  coach.reveal = true;
  await coachDraw();
  const n = coach.node;
  const t = n.target;
  fill($("#coach-feedback"),
    t ? ["The move: ", el("b", { class: "better" }, t.san),
      t.uci === n.best?.uci ? ` (the engine's move, ${n.best.score} for you).` : " (your usual move here, and a sound one).",
      " Play it to go on."] : "No move known here.",
    reasons(coach.check?.ideas));
});
$("#coach-back").addEventListener("click", () => {
  const steps = coach.mode === "play" ? 2 : 1;
  coach.pending = null;
  coachGo(coach.path.slice(0, Math.max(0, coach.path.length - steps)));
});
$("#coach-restart").addEventListener("click", coachStart);

// -- openings ------------------------------------------------------------------------------
function favoriteCard(f) {
  const decided = f.wins + f.draws + f.losses;
  const pct = (n) => `${(100 * n) / decided}%`;
  const result = f.score === null
    ? "only bullet, whose results don't count"
    : `scored ${f.score}% (${f.wins} won, ${f.draws} drawn, ${f.losses} lost${decided < f.games ? " outside bullet" : ""})`;
  const t = f.after_ten;
  return el(
    "div", { class: "card" },
    el("h3", {}, f.family),
    el("div", { class: "muted" }, `${f.games} games · ${result} · mistakes on ${f.opening_error_rate}% of your opening moves`),
    t ? el("div", { class: "small" }, `Engine eval after your 10th move (not results): ${t.median} (median of ${games(t.games)}), `,
      `${t.bad}% of them at −1 or worse; ${t.since_book} since the game left the book.`) : null,
    decided ? el("div", { class: "score" }, el("div", { class: "w", style: `width:${pct(f.wins)}` }),
      el("div", { class: "d", style: `width:${pct(f.draws)}` }), el("div", { class: "l", style: `width:${pct(f.losses)}` })) : null,
    f.idea ? el("p", { class: "idea" }, el("b", {}, "What it's about: "), f.idea) : null,
    f.line ? el("p", {}, "Your usual line: ", el("code", {}, f.line)) : null,
    f.book ? el("p", { class: "muted small" }, `Book (${f.names[0]?.name}): `, el("code", {}, f.book)) : null,
    f.fixes.length
      ? el("div", {},
        el("div", { class: "lesson" }, el("b", {}, "Improve it: "), "positions you reached in several games where your usual move loses ground."),
        el("div", { class: "examples" }, f.fixes.map((x) => el("div", { class: "example" },
          boardNode(x.svg),
          el("div", {}, "You play ", el("span", { class: "played" }, x.move), ` (${x.times} games, −${x.loss_cp} cp on average) · better `,
            el("span", { class: "better" }, x.best)),
          reasons(x.reasons),
          x.urls[0] ? el("a", { href: x.urls[0], target: "_blank", rel: "noopener" }, "an example") : null))),
        el("p", {}, el("button", { class: "primary", onclick: () => show("practice", f.family) }, "Drill these fixes")))
      : el("p", { class: "muted small" }, "No repeated weak move here: your play in this opening holds up."),
  );
}

// Every opening by the engine's eval after your 10th move: results don't come into it, so bullet
// games count. Worst first, by the mistake rate's interval and what was lost since book.
function afterTenTable(rows) {
  const range = (low, high, unit = "%") => el("span", { class: "muted small" }, ` (${low}–${high}${unit})`);
  return el("table", {},
    el("tr", {}, ["opening", "games", "eval", "at −1 or worse", "mistakes, moves 1–10", "since book"].map((h) => el("th", {}, h))),
    rows.map((a) => el("tr", {},
      el("td", {}, a.family), el("td", {}, a.games), el("td", {}, a.median),
      el("td", {}, `${a.bad}%`, range(a.bad_low, a.bad_high)),
      el("td", {}, `${a.error_rate}%`, range(a.error_low, a.error_high)),
      el("td", {}, a.since_book))));
}

async function loadOpenings() {
  const o = await api("/api/openings");
  const t = o.after_ten;
  fill($("#openings"),
    el("p", { class: "muted" }, "Your most-played openings, how you score, and where your usual move can be improved (named book moves are never flagged)."),
    el("div", { class: "columns" },
      el("div", {}, el("h2", {}, "With White"), o.white.length ? o.white.map(favoriteCard) : el("p", {}, "Not enough games yet.")),
      el("div", {}, el("h2", {}, "With Black"), o.black.length ? o.black.map(favoriteCard) : el("p", {}, "Not enough games yet."))),
    t.white.length || t.black.length ? [
      el("h2", {}, "How each opening comes out"),
      el("p", { class: "muted" }, "Engine eval after your 10th move (not results), so bullet games count too: the median, and how often you stand at −1 or worse. ",
        "Worst first, by your mistake rate on moves 1–10 (the low end of its 95% interval, in brackets), then by what you lost since the game left the opening table, ",
        "so a gambit's pawn is the book's, not yours."),
      t.white.length ? [el("h3", {}, "With White"), afterTenTable(t.white)] : null,
      t.black.length ? [el("h3", {}, "With Black"), afterTenTable(t.black)] : null,
    ] : null,
  );
}

// -- progress ------------------------------------------------------------------------------
async function loadProgress(speed) {
  const p = await api(`/api/progress${speed ? `?${new URLSearchParams({ speed })}` : ""}`);
  const rows = p.periods.map((r) => el("tr", {}, el("td", {}, r.label), el("td", {}, r.games), el("td", {}, r.score === null ? "–" : `${r.score}%`),
    el("td", {}, `${r.error_rate}%`), el("td", {}, r.acpl)));
  fill($("#progress"),
    el("h2", {}, "Your games by month"),
    el("p", { class: "muted" }, "In bullet only your first 10 moves count, and the score leaves bullet out: the clock decides those games."),
    el("table", {}, el("tr", {}, ["month", "games", "score", "mistakes + blunders", "average cp loss"].map((h) => el("th", {}, h))), rows),
    trendSection(p.trend),
    el("h2", {}, "Puzzles"),
    p.puzzles.attempts
      ? el("div", {},
        el("p", {}, `Solved ${p.puzzles.solved} of ${p.puzzles.attempts} attempts.`),
        el("table", {}, el("tr", {}, ["theme", "attempts", "solved"].map((h) => el("th", {}, h))),
          p.tags.map((t) => el("tr", {}, el("td", {}, t.tag.replaceAll("_", " ")), el("td", {}, t.attempts), el("td", {}, `${t.rate}%`)))))
      : el("p", { class: "muted" }, "No attempts yet: try the Practice tab."),
  );
}

// Your rating week by week (read from your games; bullet left out) next to the practice you did.
function trendSection(t) {
  if (!t.speed) return null;
  const signed = (n) => `${n > 0 ? "+" : ""}${n}`;
  const pick = el("select", {}, t.speeds.map((s) => el("option", s === t.speed ? { value: s, selected: "" } : { value: s }, s)));
  pick.addEventListener("change", () => loadProgress(pick.value));
  const cell = (v) => el("td", {}, v ?? "");
  const rows = t.weeks.map((w) => el("tr", {},
    cell(w.label), cell(w.games || ""), cell(w.rating),
    el("td", { class: w.change > 0 ? "better" : w.change < 0 ? "played" : "" }, w.change === null ? "" : signed(w.change)),
    cell(w.puzzles ? `${w.solved} of ${w.puzzles}` : ""), cell(w.answers ? `${w.right} of ${w.answers}` : "")));
  const e = t.effect;
  return [
    el("h2", {}, "Rating and practice by week"),
    el("p", { class: "muted" }, "Your ", t.speeds.length > 1 ? pick : t.speed,
      " rating in your last game each week (bullet is left out), next to the puzzles and opening training you did."),
    el("table", {}, el("tr", {}, ["week", "games", "rating", "change", "puzzles solved", "openings right"].map((h) => el("th", {}, h))), rows),
    e ? el("p", {}, `Weeks you practised: ${signed(e.practised)} a week on average (${e.practised_weeks} weeks); weeks you didn't: ${signed(e.idle)} (${e.idle_weeks} weeks). A comparison, not proof.`)
      : el("p", { class: "muted small" }, "Once there are four weeks of games with practice and four without, this compares how your rating moved in each."),
  ];
}

show("mistakes");
