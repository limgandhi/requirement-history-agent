"use strict";

const $ = (id) => document.getElementById(id);
const store = {
  get(key) { try { return localStorage.getItem(key) || ""; } catch (e) { return ""; } },
  set(key, value) { try { localStorage.setItem(key, value); } catch (e) { /* private window: keep going */ } },
};

// One conversation per browser; it survives a reload (the server keeps it).
let sessionId = store.get("session_id");
if (!/^[A-Za-z0-9_-]{8,64}$/.test(sessionId)) {
  sessionId = (crypto.randomUUID ? crypto.randomUUID() : String(Date.now()) + Math.random().toString(36).slice(2)).replace(/[^A-Za-z0-9_-]/g, "");
  store.set("session_id", sessionId);
}
let busy = false;
let pendingQuestions = [];
let collected = {};

// Text goes in with textContent only, so nothing the server or a document says can run as HTML.
function el(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = text;
  return node;
}

function addMessage(role, text) {
  const box = $("messages");
  const node = el("div", "msg " + role, text);
  const loading = $("loading");
  if (loading) box.insertBefore(node, loading); else box.appendChild(node);
  box.scrollTop = box.scrollHeight;
}

function setBusy(value) {
  busy = value;
  $("send").disabled = value || pendingQuestions.length > 0;  // a question is waiting: answer it first
  $("text").disabled = value;
  $("file").disabled = value;
  $("new-chat").disabled = value;
  showLoading(value);
}

// "Waiting for the answer" bubble, always the last item in the chat while a request is running.
function showLoading(on) {
  const box = $("messages");
  const old = $("loading");
  if (old) old.remove();
  if (!on) return;
  const bubble = el("div", "msg agent loading");
  bubble.id = "loading";
  bubble.setAttribute("role", "status");
  bubble.append(el("span", "spinner"), el("span", "", "답변을 생성하는 중입니다..."));
  box.appendChild(bubble);
  box.scrollTop = box.scrollHeight;
}

async function api(path, options) {
  const response = await fetch(path, options);
  let body = null;
  try { body = await response.json(); } catch (e) { /* not JSON */ }
  if (!response.ok) {
    const detail = body && (body.error || body.detail);
    throw new Error(typeof detail === "string" ? detail : "요청에 실패했습니다 (" + response.status + ")");
  }
  return body;
}

function postJson(path, data) {
  return api(path, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(data) });
}

// ---- conversation -------------------------------------------------------------------

function actorSuffix() {
  const requester = $("requester").value.trim();
  const modifier = $("modifier").value.trim();
  return requester && modifier ? "\n(요청자: " + requester + ", 수정자: " + modifier + ")" : "";
}

async function send(text, shown) {
  addMessage("user", shown || text);
  setBusy(true);
  try {
    handle(await postJson("/api/chat", { session_id: sessionId, message: text }));
  } catch (error) {
    addMessage("error", error.message);
    await restorePending();
  } finally {
    setBusy(false);
  }
}

function handle(result) {
  if (result.reply) addMessage("agent", result.reply);
  showPending(result.pending || []);
  if (!result.pending || !result.pending.length) refreshData();
}

function showPending(questions) {
  pendingQuestions = questions;
  collected = {};
  renderNextQuestion();
}

function renderNextQuestion() {
  const box = $("pending");
  box.replaceChildren();
  const open = pendingQuestions.find((q) => !(q.id in collected));
  box.hidden = !open;
  $("send").disabled = busy || !!open;
  if (!open) return;

  box.appendChild(el("p", "", open.message));
  const row = el("div", "row");
  const answer = (value) => {
    collected[open.id] = value;
    if (pendingQuestions.every((q) => q.id in collected)) submitAnswers();
    else renderNextQuestion();
  };

  if (open.kind === "confirm") {
    const yes = el("button", "primary", "예, 진행");
    const no = el("button", "", "아니오, 취소");
    yes.type = no.type = "button";
    yes.onclick = () => answer(true);
    no.onclick = () => answer(false);
    row.append(yes, no);
  } else if (open.kind === "choice") {
    (open.options || []).forEach((option) => {
      const button = el("button", "", option.label);
      button.type = "button";
      button.onclick = () => answer(option.id);
      row.appendChild(button);
    });
  } else {
    const input = el("input");
    input.placeholder = "입력하세요";
    const ok = el("button", "primary", "확인");
    ok.type = "button";
    ok.onclick = () => input.value.trim() && answer(input.value.trim());
    input.onkeydown = (event) => { if (event.key === "Enter") { event.preventDefault(); ok.click(); } };
    row.append(input, ok);
    setTimeout(() => input.focus(), 0);
  }
  box.appendChild(row);
}

async function submitAnswers() {
  setBusy(true);
  const answers = collected;
  $("pending").hidden = true;
  try {
    handle(await postJson("/api/resume", { session_id: sessionId, answers }));
  } catch (error) {
    addMessage("error", error.message);
    await restorePending();
  } finally {
    setBusy(false);
    renderNextQuestion();
  }
}

async function restorePending() {
  try {
    const state = await api("/api/session/" + sessionId);
    showPending(state.pending || []);
  } catch (e) { /* keep what is on screen */ }
}

async function restoreSession() {
  try {
    const state = await api("/api/session/" + sessionId);
    state.messages.forEach((m) => addMessage(m.role, m.text));
    showPending(state.pending || []);
  } catch (e) { /* a new conversation */ }
}

// Clear = start a fresh conversation under a new session id. The old one stays in the server DB.
$("new-chat").addEventListener("click", () => {
  if (busy) return;
  if (pendingQuestions.length && !confirm("답하지 않은 확인 요청이 있습니다. 새 대화를 시작하면 취소됩니다. 계속할까요?")) return;
  sessionId = (crypto.randomUUID ? crypto.randomUUID() : String(Date.now()) + Math.random().toString(36).slice(2)).replace(/[^A-Za-z0-9_-]/g, "");
  store.set("session_id", sessionId);
  $("messages").replaceChildren();
  showPending([]);
  $("text").value = "";
  $("text").focus();
});

$("form").addEventListener("submit", (event) => {
  event.preventDefault();
  const text = $("text").value.trim();
  if (!text || busy || pendingQuestions.length) return;
  $("text").value = "";
  send(text + actorSuffix(), text);
});
$("text").addEventListener("keydown", (event) => {
  if (event.key === "Enter" && !event.shiftKey && !event.isComposing) { event.preventDefault(); $("form").requestSubmit(); }
});

["requester", "modifier"].forEach((id) => {
  $(id).value = store.get(id);
  $(id).addEventListener("input", () => store.set(id, $(id).value));
});

// ---- upload ---------------------------------------------------------------------------

$("file").addEventListener("change", async (event) => {
  const file = event.target.files[0];
  event.target.value = "";
  if (!file || busy) return;
  if (!$("requester").value.trim() || !$("modifier").value.trim()) {
    addMessage("error", "문서를 올리기 전에 위에 요청자와 수정자를 입력해 주세요.");
    return;
  }
  setBusy(true);
  try {
    const form = new FormData();
    form.append("file", file);
    const uploaded = await api("/api/upload", { method: "POST", body: form });
    setBusy(false);
    await send("업로드한 문서 " + uploaded.path + " 를 입력해줘" + actorSuffix(), "문서 올림: " + uploaded.name);
  } catch (error) {
    addMessage("error", error.message);
    setBusy(false);
  }
});

// ---- requirements, history, documents -------------------------------------------------

const when = (text) => (text || "").slice(0, 16);

async function loadRequirements() {
  const rows = await api("/api/requirements");
  const panel = $("panel-req");
  panel.replaceChildren();
  if (!rows.length) { panel.appendChild(el("p", "empty", "아직 요구사항이 없습니다. 문서를 올려 입력해 보세요.")); return; }
  const table = el("table");
  const head = el("tr");
  ["ID", "제목", "상태"].forEach((h) => head.appendChild(el("th", "", h)));
  table.appendChild(head);
  rows.forEach((r) => {
    const tr = el("tr", "clickable");
    tr.tabIndex = 0;
    tr.appendChild(el("td", r.status === "deleted" ? "deleted" : "", r.req_key));
    tr.appendChild(el("td", r.status === "deleted" ? "deleted" : "", r.title));
    const status = el("td");
    status.appendChild(el("span", "badge", r.status === "active" ? "사용 중" : "삭제됨"));
    tr.appendChild(status);
    const open = () => { document.querySelectorAll("tr.selected").forEach((n) => n.classList.remove("selected")); tr.classList.add("selected"); showHistory(r); };
    tr.onclick = open;
    tr.onkeydown = (event) => { if (event.key === "Enter") open(); };
    table.appendChild(tr);
  });
  panel.appendChild(table);
}

const ACTIONS = { add: "추가", update: "수정", delete: "삭제" };
const SOURCES = { manual: "수동", auto: "자동" };

async function showHistory(requirement) {
  const box = $("history");
  box.hidden = false;
  box.replaceChildren(el("h2", "", requirement.req_key + " " + requirement.title + " 이력"));
  box.appendChild(el("p", "", requirement.content));
  const rows = await api("/api/requirements/" + requirement.req_key + "/history");
  const list = el("ul");
  rows.forEach((h) => {
    const item = el("li");
    const doc = h.version ? h.document + " v" + h.version : h.document;
    item.appendChild(el("div", "", ACTIONS[h.action] + " · " + SOURCES[h.source_type] + " · " + doc + " · " + when(h.created_at)));
    item.appendChild(el("div", "", "요청자 " + h.requester + " / 수정자 " + h.modifier));
    if (h.old_value) item.appendChild(el("div", "old", "이전: " + h.old_value));
    if (h.new_value) item.appendChild(el("div", "new", "새 값: " + h.new_value));
    list.appendChild(item);
  });
  box.appendChild(list);
}

async function loadDocuments() {
  const rows = await api("/api/documents");
  const panel = $("panel-doc");
  panel.replaceChildren();
  if (!rows.length) { panel.appendChild(el("p", "empty", "입력된 문서가 없습니다.")); return; }
  const table = el("table");
  const head = el("tr");
  ["문서", "종류", "입력 시각", ""].forEach((h) => head.appendChild(el("th", "", h)));
  table.appendChild(head);
  rows.forEach((d) => {
    const tr = el("tr");
    tr.appendChild(el("td", "", d.name + " v" + d.version));
    tr.appendChild(el("td", "", d.kind === "generated" ? "새 버전(생성)" : "입력"));
    tr.appendChild(el("td", "", when(d.created_at)));
    const link = el("td");
    if (d.kind === "generated") {
      const a = el("a", "", "내려받기");
      a.href = "/api/files/" + d.id;
      link.appendChild(a);
    }
    tr.appendChild(link);
    table.appendChild(tr);
  });
  panel.appendChild(table);
}

async function refreshData() {
  try { await Promise.all([loadRequirements(), loadDocuments()]); } catch (e) { /* leave the old lists */ }
}

document.querySelectorAll(".tabs [data-tab]").forEach((tab) => {
  tab.addEventListener("click", () => {
    document.querySelectorAll(".tabs [data-tab]").forEach((t) => t.setAttribute("aria-selected", String(t === tab)));
    $("panel-req").hidden = tab.dataset.tab !== "req";
    $("panel-doc").hidden = tab.dataset.tab !== "doc";
    $("history").hidden = tab.dataset.tab !== "req" || !$("history").childNodes.length;
  });
});
$("refresh").addEventListener("click", refreshData);

restoreSession();
refreshData();
