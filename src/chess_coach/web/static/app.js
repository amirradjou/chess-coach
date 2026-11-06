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

const TAG_NAMES = {
  punish_capture: "punish with a capture",
  punish_check: "punish with a check",
  punish_quiet: "punish with a quiet move",
};
const tagName = (t) => TAG_NAMES[t] ?? t.replaceAll("_", " ");

// -- tabs ----------------------------------------------------------------------------------
const loaded = {};
function show(tab, arg) {
  for (const b of document.querySelectorAll("nav button")) b.classList.toggle("on", b.dataset.tab === tab);
  // Today is Practice's board, served from today's session
  const section = tab === "today" ? "practice" : tab;
  for (const s of document.querySelectorAll("main section")) s.hidden = s.id !== section;
  if (tab === "today") return openToday();
  if (tab === "practice") return openPractice(arg);
  if (tab === "coach") return openCoach();
  if (!loaded[tab]) loaded[tab] = { mistakes: loadMistakes, openings: loadOpenings, progress: loadProgress }[tab]();
}
for (const b of document.querySelectorAll("nav button")) b.addEventListener("click", () => show(b.dataset.tab));

// -- what you get wrong --------------------------------------------------------------------
function example(e, label) {
  return el(
    "div", { class: "example" },
    label ? el("div", { class: "threat small" }, label) : null,
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
    el("div", { class: "examples" }, h.examples.map((e) => example(e))),
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

// Ignored threats and unsafe moves: their share of your mistakes next to your opponents'. A step
// is only called yours to work on when the difference's interval is above zero.
function threatSection(t) {
  const share = (x) => [`${x.rate}%`, el("div", { class: "muted small" }, `${x.low}–${x.high}%`)];
  const mine = t.steps.filter((s) => s.yours);
  return [
    el("h2", {}, "Their threats and your blunder check"),
    el("p", { class: "muted" },
      `Before each counted mistake or blunder the engine let the mover pass, to see what the other side threatened: ${t.you} of your mistakes and blunders, and ${t.them} of your opponents' as the comparison.` +
      (t.unchecked ? ` ${t.unchecked} aren't checked yet (run chess-coach threats).` : "") +
      " Orange on the boards is their threat, or the reply your move allowed."),
    el("table", {},
      el("tr", {}, ["what happened", "you", "your opponents", "compared"].map((h) => el("th", {}, h))),
      t.steps.map((s) => el("tr", {},
        el("td", {}, el("b", {}, s.title), el("div", { class: "muted small" }, s.what)),
        el("td", {}, share(s.you)),
        el("td", {}, share(s.them)),
        el("td", {}, s.verdict, el("div", { class: "muted small" }, `${signed(s.diff.points)} points (${signed(s.diff.low)} to ${signed(s.diff.high)})`))))),
    mine.length
      ? mine.map((s) => el("div", { class: "lesson" }, el("b", {}, `${s.title}: `), `a step to work on, it's in more of your mistakes than of your opponents'. Practise: ${s.tip}.`))
      : el("p", { class: "small" }, "Neither is clearly more common in your mistakes than in your opponents' yet: the 95% interval of each difference includes zero."),
    el("div", { class: "examples" }, t.steps.flatMap((s) => s.examples.map((e) => example(e, s.title)))),
  ];
}

async function loadMistakes() {
  const punishing = api("/api/punish"); // its own request: the page doesn't wait for it
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
    el("p", { class: "muted" }, "Counted from Stockfish's verdict on your own moves. Red is what you played, green what to play, orange their threat."),
    o.habits.length ? o.habits.map((h) => habitCard(h, tags)) : el("p", {}, "Nothing stands out yet."),
    repeated.length ? [
      el("h2", {}, "The same mistake in the same position"),
      el("p", { class: "muted" }, "Your inaccuracies or worse from the very same position in 3 or more games (named book moves left out). Red is what you played, green the better move."),
      el("div", { class: "card" }, el("div", { class: "examples" }, repeated.map(repeatCard))),
    ] : null,
    el("h2", {}, "When it happens"),
    el("p", { class: "muted" }, "Situations where your mistake rate is clearly above your own average."),
    o.situations.length ? o.situations.map((h) => habitCard(h, tags)) : el("p", {}, "No situation stands out yet: analyse more games."),
    o.threats ? threatSection(o.threats) : null,
    el("div", { id: "punish" }),
    o.pressure.length ? [
      el("h2", {}, "Short on time, but it keeps happening"),
      el("p", { class: "muted" }, "Fast moves are reflexes, so they only count here when the same mistake shows up again and again (15+ times across 8+ games)."),
      o.pressure.map((h) => habitCard(h, tags)),
    ] : null,
  );
  punishing.then((p) => p.gifts && fill($("#punish"), punishSection(p, tagList)))
    .catch((err) => fill($("#punish"), el("p", { class: "muted" }, `When they blunder: ${err.message}`)));
}

// -- when they blunder ---------------------------------------------------------------------
// Your miss rate on their blunders next to theirs on yours (coach/punish.py), each with its 95%
// interval; a gap is only called one when its interval leaves out zero.
const signed = (n) => `${n > 0 ? "+" : ""}${n}`;
const points = (n) => `${n > 0 ? "+" : ""}${n.toFixed(1)}`;

function rateCell(r) {
  return el("td", {}, r.n ? `${r.rate.toFixed(1)}% of ${r.n}` : "–",
    r.n ? el("div", { class: "muted small" }, `${r.low.toFixed(1)}–${r.high.toFixed(1)}%`) : null);
}

// No gap when a side had no chances (0 of 0 is no rate).
function gapCell(c) {
  if (c.gap == null) return el("td", {}, "–");
  return el("td", { class: c.verdict === "worse" ? "played" : c.verdict === "better" ? "better" : "" }, points(c.gap),
    el("div", { class: "muted small" }, `${points(c.interval[0])} to ${points(c.interval[1])}`));
}

function rateTable(head, rows, sides = ["you missed", "they missed"]) {
  return el("table", {},
    el("tr", {}, [head, ...sides, "gap (95%)"].map((h) => el("th", {}, h))),
    rows.map((c) => el("tr", {}, el("td", {}, c.name ?? c.label), rateCell(c.yours), rateCell(c.theirs), gapCell(c))));
}

function missExample(m) {
  return el(
    "div", { class: "example" },
    boardNode(m.svg),
    el("div", {}, "They played ", el("b", {}, m.theirs), " · you ", el("span", { class: "played" }, m.move),
      " · punish with ", el("span", { class: "better" }, m.best),
      el("span", { class: "muted small" }, ` · win chance −${m.loss.toFixed(1)}%`)),
    reasons(m.reasons),
    m.url ? el("a", { href: m.url, target: "_blank", rel: "noopener" }, "open the game") : null,
  );
}

function punishSection(p, tagList) {
  const drills = tagList.filter((t) => t.tag.startsWith("punish_"));
  const focus = p.focus.length ? ` (${p.focus.join(", ").toLowerCase()})` : "";
  return [
    el("h2", {}, "When they blunder"),
    el("p", { class: "muted" }, "A blunder here is a move that loses 20+ points of win probability. Your reply keeps the gift when it gives back under 10 points, takes only part of it at 10–20 (a partial miss) and misses it at 20 or more (a clear miss). Your opponents' replies to your blunders are the yardstick: players at your level with the same kind of chance. In bullet only moves 1–10 count, and replies made short on time don't."),
    // Partial and clear misses apart (different habits, each with its own gap), then together.
    p.groups.map((g) => el("div", { class: "card" }, el("h3", {}, g.label), el("div", {}, g.headline),
      rateTable("your reply gave back", [g.partial, g.clear, g.missed], ["you", "they"]))),
    p.partial_lesson ? [
      el("div", { class: "lesson" }, el("b", {}, "What to learn: "), p.partial_lesson),
      p.partials.length ? [
        el("h3", {}, `Where you took only part of it (${p.partial_focus.join(", ").toLowerCase()})`),
        el("div", { class: "examples" }, p.partials.map(missExample)),
      ] : null,
    ] : null,
    el("h3", {}, "Missed, by the move that punishes"),
    el("p", { class: "muted small" }, "Here, in the trend and in the openings below, a miss is partial or clear together: your reply gave back 10 points or more."),
    rateTable("punishing move", p.kinds),
    p.lessons.length
      ? p.lessons.map((l) => el("div", { class: "lesson" }, el("b", {}, "What to learn: "), l))
      : el("p", { class: "muted small" }, "No kind of punishing move stands out against your opponents: everyone misses some kinds more than others."),
    drills.length ? el("p", {}, "Practise the punishments you missed outright (puzzles come from moves losing 20+ points): ", drills.flatMap((t) => [
      el("button", { class: "chip", onclick: () => show("practice", t.tag) }, `${tagName(t.tag)} (${t.puzzles})`), " "])) : null,
    p.trend.length > 1 ? [
      el("h3", {}, "Over time"),
      el("p", { class: "muted small" }, "Each row is a stretch of games holding about 150 of their blunders you could punish (the oldest may hold fewer); both rates in a row come from the same games."),
      rateTable("games played", p.trend),
    ] : null,
    // Openings only where you clearly miss more (inside a group where you do), each with examples
    // of the part behind most of its gap; otherwise the examples in p.worst (worst_title).
    p.families.length ? [
      el("h3", {}, `Where you miss more than your opponents, partial and clear together${focus}`),
      p.families.map((f) => el("div", { class: "card" },
        el("h3", {}, f.label),
        el("div", { class: "muted" }, `You missed ${f.yours.missed} of ${f.yours.n} (${f.yours.rate.toFixed(1)}%); they missed ${f.theirs.missed} of ${f.theirs.n} (${f.theirs.rate.toFixed(1)}%): ${points(f.gap)} points (95% interval ${points(f.interval[0])} to ${points(f.interval[1])}); ${f.part_note}.`),
        el("div", { class: "examples" }, f.examples.map(missExample)))),
    ] : p.worst.length ? [
      el("h3", {}, p.worst_title),
      el("div", { class: "examples" }, p.worst.map(missExample)),
    ] : null,
    el("p", { class: "muted small" }, "Learn openings has a deck of the mistakes your opponents make in your openings' first 8 moves (Punish their mistakes); this counts every move that counts."),
  ];
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

let todayMode = true;

function fresh(puzzle) {
  return { puzzle, solving: true, tries: [], result: null, lines: [], line: 0, ply: 0, from: null, asking: false, threat: null, arrow: null };
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

async function openToday() {
  todayMode = true;
  $("#tag-filter").hidden = true;
  await nextPuzzle();
}

async function openPractice(tag) {
  todayMode = false;
  $("#tag-filter").hidden = false;
  const tags = await api("/api/drill/tags");
  const select = $("#tag");
  const keep = tag ?? select.value;
  fill(select, el("option", { value: "" }, "everything"),
    tags.map((t) => el("option", { value: t.tag }, `${tagName(t.tag)} (${t.due} due, ${t.new} new)`)));
  select.value = keep || "";
  await nextPuzzle();
}
$("#tag").addEventListener("change", nextPuzzle);

function buttons({ retry = false, giveup = false, next = false, skip = false }) {
  $("#skip").hidden = !skip;
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
  $("#today-done").hidden = true;
  $("#practice .drill").hidden = false;
  if (todayMode) return nextToday();
  countToday();
  const tag = $("#tag").value;
  const p = await api("/api/drill/next" + (tag ? `?tag=${encodeURIComponent(tag)}` : ""));
  if (todayMode) return; // you moved on to Today while this came back
  if (p.done) {
    fill($("#board"));
    $("#prompt").textContent = p.puzzles
      ? "Nothing due here today. Solved puzzles come back later; missed ones tomorrow."
      : "No puzzles yet. Run `chess-coach puzzles make` (and `--openings`).";
    $("#due").textContent = "";
    return;
  }
  drill = fresh(p);
  $("#due").textContent = `${p.again} due again · ${p.new} new`;
  if (p.ask_threat) return askThreat(p);
  await startPuzzle();
}

// -- today ---------------------------------------------------------------------------------
function showProgress(p) {
  $("#today-count").textContent = p.total ? `${p.done} of ${p.total}` : "";
}

// The tab's count, when you practise elsewhere: an answer counts wherever you gave it.
async function countToday() {
  showProgress((await api("/api/today/next")).progress);
}

async function nextToday() {
  const p = await api("/api/today/next");
  if (!todayMode) return; // you moved on to Practice while this came back
  showProgress(p.progress);
  if (p.done) {
    $("#due").textContent = "";
    return todayDone(p);
  }
  drill = fresh(p);
  $("#due").textContent = `${p.progress.done} of ${p.progress.total} · ${p.progress.again} due again · ${p.progress.new} new · about ${p.progress.minutes} min`;
  if (p.kind === "card") return startCard();
  if (p.ask_threat) return askThreat(p);
  await startPuzzle();
}

function rightOf({ right, tried }) {
  return tried ? `${right} of ${tried} right (${Math.round((100 * right) / tried)}%)` : "none yet";
}

function todayDone(p) {
  $("#practice .drill").hidden = true;
  const panel = $("#today-done");
  panel.hidden = false;
  const more = el("button", { class: "primary" }, "Practise more");
  more.addEventListener("click", () => show("practice"));
  const heading = p.progress.done
    ? `Done for today: ${p.progress.done} practised.`
    : p.puzzles ? "Nothing to practise today." : "No puzzles yet.";
  fill(panel,
    el("h2", {}, heading),
    p.puzzles
      ? [
        el("p", {}, `Tomorrow: ${p.tomorrow.again} due again, ${p.tomorrow.new} new.`),
        el("p", {}, `You practised on ${p.days.practised} of the last ${p.days.of} days.`),
        el("p", {}, `Reviews: ${rightOf(p.reviews)}. First tries: ${rightOf(p.first)}.`),
        el("p", { class: "muted small" }, "A first try shows what you already knew; a review shows whether the practice stuck."),
        p.tomorrow.new ? el("div", { class: "buttons" }, more) : null,
      ]
      : el("p", {}, "Run ", el("code", {}, "chess-coach sync"), " to bring in your games and make puzzles from your mistakes."),
  );
}

// An opening position that is due again, from Learn openings. One answer, then the move to learn.
async function startCard() {
  const p = drill.puzzle;
  drill.asking = false;
  fill($("#prompt"),
    p.punish
      ? el("span", {}, `Your ${p.opening}: their last move was a mistake you've met before. Punish it.`)
      : el("span", {}, `Your ${p.opening}: what do you play here?`),
    p.line ? [el("br"), el("span", { class: "muted small" }, p.line)] : null,
    el("br"), el("span", { class: "muted small" }, `${p.side === "white" ? "White" : "Black"} to move · opening training`),
    p.opening_idea ? el("span", { class: "idea" }, el("b", {}, `${p.opening}: `), p.opening_idea) : null,
  );
  buttons({ giveup: true });
  await draw();
}

async function answerCard(uci) {
  const card = drill;
  let r;
  try {
    r = await api("/api/today/card", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ id: card.puzzle.id, uci }),
    });
  } catch (err) {
    if (drill === card) fill($("#feedback"), "That isn't a legal move here. Try again.");
    return;
  }
  if (drill !== card) return; // you moved on: the answer is saved, and this board is gone
  drill.solving = false;
  drill.arrow = r.target?.uci ?? null;
  const also = r.correct && r.target && r.target.san !== r.answer ? ` The move to remember here: ${r.target.san}.` : "";
  fill($("#feedback"),
    r.correct
      ? [el("b", { class: "better" }, `${r.answer}: good.`), also]
      : [
        r.answer ? [el("b", { class: "played" }, `${r.answer} isn't it`), r.loss !== null ? `: it gives away ${r.loss}. ` : ". "] : null,
        r.target ? ["The move: ", el("b", { class: "better" }, r.target.san), ". "] : null,
      ],
    reasons(r.faults),
    el("br"), el("span", { class: "muted small" }, `Next time: ${r.next_due}.`),
  );
  buttons({ next: true });
  await draw();
}

function puzzlePrompt(p) {
  fill($("#prompt"),
    p.opening_fix
      ? el("span", {}, `Your opening (${p.opening || ""}): here you usually play `, el("span", { class: "played" }, p.played), ". Find a better move.")
      : el("span", {}, "In the game you played ", el("span", { class: "played" }, p.played), ". Find the best move."),
    el("br"), el("span", { class: "muted small" }, `${p.side === "white" ? "White" : "Black"} to move · ${p.tags.map(tagName).join(", ")}`),
    p.opening_idea ? el("span", { class: "idea" }, el("b", {}, `${p.opening}: `), p.opening_idea) : null,
  );
}

async function startPuzzle() {
  drill.asking = false;
  puzzlePrompt(drill.puzzle);
  buttons({ giveup: true });
  await draw();
}

// A puzzle from a threat you ignored starts with a question: what does their last move threaten?
// Graded leniently and not recorded; then the puzzle, with their threat drawn in orange.
async function askThreat(p) {
  drill.asking = true;
  fill($("#prompt"),
    el("span", {}, "They just played ", el("b", {}, p.ask_threat.last), ". What do they threaten?"),
    el("br"), el("span", { class: "muted small" }, "Click their piece, then the square it would go to. Then find your move."));
  buttons({ skip: true });
  await draw();
}

async function guessThreat(uci) {
  let r;
  try {
    r = await api("/api/drill/threat", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ id: drill.puzzle.id, uci }),
    });
  } catch (err) {
    fill($("#feedback"), "That isn't a move they can play here. Try again, or skip the question.");
    return;
  }
  drill.threat = r.threat.uci;
  fill($("#feedback"),
    r.right ? el("b", { class: "better" }, "Yes. ") : [el("b", { class: "played" }, `Not ${r.guess}`), ": they threaten ", el("b", { class: "threat" }, r.threat.san), ". "],
    r.why);
  await startPuzzle();
}

// The position on show: the puzzle itself (ply 0) or the position after `ply` moves of a line,
// with the line's next move as an arrow.
function shown() {
  if (drill.puzzle.kind === "card") {
    return { fen: drill.puzzle.fen, last: drill.puzzle.last, next: drill.arrow, color: "best" };
  }
  const line = drill.lines[drill.line];
  const before = drill.ply > 0 ? line.plies[drill.ply - 1] : null;
  if (!line && drill.puzzle.ask_threat) {
    // Their last move, and once the question is answered, their threat.
    return { fen: drill.puzzle.fen, last: drill.puzzle.ask_threat.last_uci, next: drill.threat, color: "threat" };
  }
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
  if (!drill) return; // the next item is on its way; the old board is still showing
  const svg = evt.currentTarget;
  const sq = squareAt(svg, evt, drill.puzzle.side);
  if (!sq) return;
  if (!drill.from) { drill.from = sq; mark(svg, sq, drill.puzzle.side); return; }
  const uci = drill.from.name + sq.name;
  drill.from = null;
  mark(svg, null);
  if (uci.slice(0, 2) === uci.slice(2)) return;
  if (drill.asking) return guessThreat(uci);
  if (drill.puzzle.kind === "card") return drill.solving ? answerCard(uci) : undefined;
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
$("#skip").addEventListener("click", () => { fill($("#feedback")); startPuzzle(); });
$("#giveup").addEventListener("click", () => {
  if (!drill) return;
  if (drill.puzzle.kind === "card") return answerCard("?");
  return drill.result ? reveal(null) : answer("?");
});
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
// The engine's eval after both sides' 10th moves in the games through here (the median; not
// results), named in full on moves too: after "you scored 0%" a bare "+0.1" reads like a result.
const afterTen = (x) => (!x.after_ten ? ""
  : ` · engine eval after both sides' 10th moves: ${x.after_ten.median} (median of ${games(x.after_ten.games)}; not results)`);
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
      ? `${who} · reached in ${n.games} of your games${scored(n, " · ", "from here you scored")}${afterTen(n)}`
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
    t ? el("div", { class: "small" }, `Engine eval after both sides' 10th moves (not results): ${t.median} (median of ${games(t.games)}), `,
      `${t.bad}% of them at −1 or worse; ${t.since_book} since book.`) : null,
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

// Every opening by the engine's eval after both sides' 10th moves: results don't come into it, so
// bullet games count. Worst first, by the mistake rate's interval and what was lost since book.
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
      el("p", { class: "muted" }, "Engine eval after both sides' 10th moves (not results), so bullet games count too: the median, and how often you stand at −1 or worse. ",
        "Worst first, by your mistake rate on moves 1–10 (the low end of its 95% interval, in brackets), then by what you lost since book, so a gambit's pawn is the book's, not yours. ",
        "Since book counts from the last position the opening table names, or from just before it when a White move reached it (that move then counts too), so both sides make the same number of moves."),
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
    levelsSection(p.levels),
    el("h2", {}, "Your games by month"),
    el("p", { class: "muted" }, "In bullet only your first 10 moves count, and the score leaves bullet out: the clock decides those games."),
    el("table", {}, el("tr", {}, ["month", "games", "score", "mistakes + blunders", "average cp loss"].map((h) => el("th", {}, h))), rows),
    trendSection(p.trend),
    el("h2", {}, "Puzzles"),
    p.puzzles.attempts
      ? el("div", {},
        el("p", {}, `Solved ${p.puzzles.solved} of ${p.puzzles.attempts} attempts.`),
        el("table", {}, el("tr", {}, ["theme", "attempts", "solved"].map((h) => el("th", {}, h))),
          p.tags.map((t) => el("tr", {}, el("td", {}, tagName(t.tag)), el("td", {}, t.attempts), el("td", {}, `${t.rate}%`)))))
      : el("p", { class: "muted" }, "No attempts yet: try the Practice tab."),
  );
}

// How often you go wrong early in a game, against public players by rating. The sentences come
// from the server, which only says what the interval allows.
function levelsSection(l) {
  if (!l.speeds.length) return null;
  const rate = (x) => (x
    ? [`${x.rate}%`, el("div", { class: "muted small" }, `${x.low}–${x.high}%`)]
    : "–");
  return [
    el("h2", {}, "The next level"),
    l.headline ? el("p", {}, l.headline) : null,
    l.speeds.map((s) => el("div", { class: "card" },
      el("b", {}, `${s.speed[0].toUpperCase()}${s.speed.slice(1)}`),
      el("span", { class: "muted" }, `: ${s.games} games at ${s.controls.join(" or ")}${s.rating ? `, your rating about ${s.rating}` : ""}`),
      el("table", {},
        el("tr", {}, ["moves", "you", "your opponents", "300 points above you"].map((h) => el("th", {}, h))),
        s.rows.map((r) => el("tr", {},
          el("td", {}, r.segment),
          el("td", {}, rate(r.you)),
          el("td", {}, rate(r.them)),
          el("td", {}, r.above === null ? "–" : `${r.above}%`)))),
      s.rows.map((r) => el("p", { class: "small" }, r.sentence)),
      s.other_games ? el("p", { class: "muted small" }, `${games(s.other_games).replace(" ", " more ")} at other time controls aren't counted: the public games have no yardstick for them.`) : null,
      s.stale ? el("p", { class: "muted small" }, s.stale) : null,
    )),
    el("p", { class: "muted small" }, `From your last 12 months. Mistakes and blunders (20+ points of win probability), with 95% intervals. The public games are unanalysed Lichess games, scored the same way (built ${l.built}).`),
  ];
}

// Your rating week by week (read from your games; bullet left out) next to the practice you did.
function trendSection(t) {
  if (!t.speed) return null;
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

// Today first, and fast. The header's summary comes from What you get wrong, whose first read takes
// seconds: it starts once there is something to practise on the board.
show("today").finally(() => { loaded.mistakes ??= loadMistakes(); });
