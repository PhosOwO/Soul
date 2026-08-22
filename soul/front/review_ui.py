from __future__ import annotations


def render_review_page() -> str:
    return """<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Soul Review</title>
  <style>
    :root {
      color-scheme: light;
      --bg: #f6f7f9;
      --panel: #ffffff;
      --text: #1f2933;
      --muted: #65717f;
      --line: #d8dee6;
      --focus: #1264a3;
      --ok: #13795b;
      --warn: #a15c00;
      --danger: #b42318;
      --soft-ok: #e7f4ef;
      --soft-warn: #fff4df;
      --soft-danger: #fde8e5;
      --shadow: 0 12px 30px rgba(31, 41, 51, 0.12);
    }
    * { box-sizing: border-box; }
    body {
      margin: 0;
      background: var(--bg);
      color: var(--text);
      font: 14px/1.5 system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
    }
    main {
      width: min(780px, calc(100vw - 28px));
      margin: 28px auto;
    }
    header {
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 16px;
      margin-bottom: 18px;
    }
    h1 {
      margin: 0;
      font-size: 22px;
      line-height: 1.2;
      font-weight: 700;
    }
    .subtle { color: var(--muted); }
    .panel {
      background: var(--panel);
      border: 1px solid var(--line);
      box-shadow: var(--shadow);
      border-radius: 8px;
      overflow: hidden;
    }
    .toolbar {
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 12px;
      padding: 12px 14px;
      border-bottom: 1px solid var(--line);
    }
    .counts {
      display: flex;
      gap: 8px;
      flex-wrap: wrap;
      align-items: center;
    }
    .pill {
      display: inline-flex;
      min-height: 24px;
      align-items: center;
      border: 1px solid var(--line);
      border-radius: 999px;
      padding: 2px 9px;
      color: var(--muted);
      background: #fff;
      font-size: 12px;
      white-space: nowrap;
    }
    .pill.ready { color: var(--ok); background: var(--soft-ok); border-color: #b8ddce; }
    .pill.review { color: var(--warn); background: var(--soft-warn); border-color: #efd49b; }
    section {
      padding: 14px;
      border-bottom: 1px solid var(--line);
    }
    section:last-child { border-bottom: 0; }
    h2 {
      margin: 0 0 10px;
      font-size: 13px;
      line-height: 1.3;
      letter-spacing: 0;
      text-transform: uppercase;
      color: var(--muted);
    }
    .candidate {
      border: 1px solid var(--line);
      border-radius: 8px;
      background: #fff;
      padding: 12px;
      margin-bottom: 10px;
    }
    .candidate:last-child { margin-bottom: 0; }
    .candidate[data-kind="needs_review"] { border-left: 4px solid var(--warn); }
    .candidate[data-kind="ready_to_confirm"] { border-left: 4px solid var(--ok); }
    .candidate-top {
      display: flex;
      justify-content: space-between;
      gap: 12px;
      align-items: flex-start;
    }
    .statement {
      font-size: 15px;
      font-weight: 650;
      margin: 0 0 5px;
      overflow-wrap: anywhere;
    }
    .reason {
      margin: 0;
      color: var(--muted);
      overflow-wrap: anywhere;
    }
    .source {
      color: var(--muted);
      font-size: 12px;
      white-space: nowrap;
      margin-top: 2px;
    }
    .actions {
      display: flex;
      flex-wrap: wrap;
      gap: 8px;
      margin-top: 12px;
    }
    button {
      border: 1px solid var(--line);
      border-radius: 6px;
      background: #fff;
      color: var(--text);
      min-height: 32px;
      padding: 5px 10px;
      font: inherit;
      cursor: pointer;
    }
    button:hover { background: #f2f5f8; }
    button:focus-visible {
      outline: 3px solid rgba(18, 100, 163, 0.28);
      outline-offset: 2px;
      border-color: var(--focus);
    }
    button.primary {
      background: var(--focus);
      border-color: var(--focus);
      color: #fff;
    }
    button.danger { color: var(--danger); border-color: #efb4ae; background: #fff; }
    button.ghost { color: var(--muted); }
    details {
      margin-top: 10px;
      border-top: 1px solid var(--line);
      padding-top: 8px;
    }
    summary {
      cursor: pointer;
      color: var(--focus);
      font-weight: 600;
    }
    pre {
      white-space: pre-wrap;
      word-break: break-word;
      background: #f6f7f9;
      border: 1px solid var(--line);
      border-radius: 6px;
      padding: 8px;
      max-height: 220px;
      overflow: auto;
    }
    dialog {
      border: 1px solid var(--line);
      border-radius: 8px;
      box-shadow: var(--shadow);
      width: min(560px, calc(100vw - 28px));
      padding: 0;
    }
    dialog::backdrop { background: rgba(31, 41, 51, 0.32); }
    .dialog-body { padding: 16px; }
    label {
      display: block;
      font-weight: 650;
      margin: 10px 0 5px;
    }
    textarea, input {
      width: 100%;
      border: 1px solid var(--line);
      border-radius: 6px;
      min-height: 36px;
      padding: 8px;
      font: inherit;
    }
    textarea { min-height: 96px; resize: vertical; }
    .empty, .error {
      padding: 28px 14px;
      text-align: center;
      color: var(--muted);
    }
    .error { color: var(--danger); background: var(--soft-danger); }
    @media (max-width: 560px) {
      main { margin-top: 14px; }
      header, .toolbar, .candidate-top { flex-direction: column; align-items: stretch; }
      .source { white-space: normal; }
      button { flex: 1 1 auto; }
    }
  </style>
</head>
<body>
  <main>
    <header>
      <div>
        <h1>Soul Review</h1>
        <div class="subtle" id="project">Loading project...</div>
      </div>
      <button type="button" class="ghost" id="refresh">Refresh</button>
    </header>
    <div class="panel" aria-live="polite">
      <div class="toolbar">
        <div class="counts" id="counts"></div>
        <div class="subtle" id="generated"></div>
      </div>
      <div id="content" class="empty">Loading review decisions...</div>
    </div>
  </main>

  <dialog id="editDialog">
    <form method="dialog" class="dialog-body" id="editForm">
      <h2>Edit Decision</h2>
      <input type="hidden" id="editCandidateId">
      <label for="editStatement">Candidate state</label>
      <textarea id="editStatement" required></textarea>
      <label for="editReason">Reason</label>
      <textarea id="editReason"></textarea>
      <label for="editScope">Scope</label>
      <input id="editScope">
      <div class="actions">
        <button type="submit" class="primary" value="save">Save</button>
        <button type="button" id="cancelEdit">Cancel</button>
      </div>
    </form>
  </dialog>

  <script>
    const state = { card: null, activeCandidate: null };
    const content = document.getElementById("content");
    const counts = document.getElementById("counts");
    const project = document.getElementById("project");
    const generated = document.getElementById("generated");
    const dialog = document.getElementById("editDialog");

    document.getElementById("refresh").addEventListener("click", loadCard);
    document.getElementById("cancelEdit").addEventListener("click", () => dialog.close());
    document.getElementById("editForm").addEventListener("submit", async event => {
      event.preventDefault();
      await postAction("/review/edit", {
        candidate_id: document.getElementById("editCandidateId").value,
        statement: document.getElementById("editStatement").value,
        reason: document.getElementById("editReason").value,
        scope: document.getElementById("editScope").value,
      });
      dialog.close();
      await loadCard();
    });

    async function loadCard() {
      content.className = "empty";
      content.textContent = "Loading review decisions...";
      try {
        const response = await fetch("/review-card?limit=5");
        if (!response.ok) throw new Error(await response.text());
        state.card = await response.json();
        renderCard(state.card);
      } catch (error) {
        content.className = "error";
        content.textContent = String(error);
      }
    }

    function renderCard(card) {
      project.textContent = card.project_dir || card.project || "";
      if (card.generated_at) {
        generated.textContent = formatGeneratedAt(card.generated_at);
        generated.title = card.generated_at;
      } else {
        generated.textContent = "";
        generated.removeAttribute("title");
      }
      counts.innerHTML = "";
      counts.append(
        pill(`${card.counts.total} total`),
        pill(`${card.counts.ready_to_confirm} ready`, "ready"),
        pill(`${card.counts.needs_review} review`, "review"),
      );
      if (!card.has_reviewable_content) {
        content.className = "empty";
        content.textContent = "No high-quality state decisions to review.";
        return;
      }
      content.className = "";
      content.innerHTML = "";
      content.append(
        section("Ready to Confirm", card.ready_to_confirm, "ready_to_confirm"),
        section("Needs Review", card.needs_review, "needs_review"),
      );
    }

    function pill(text, kind = "") {
      const element = document.createElement("span");
      element.className = `pill ${kind}`;
      element.textContent = text;
      return element;
    }

    function formatGeneratedAt(value) {
      const generatedAt = new Date(value);
      if (Number.isNaN(generatedAt.getTime())) return "";
      const seconds = Math.max(0, Math.floor((Date.now() - generatedAt.getTime()) / 1000));
      if (seconds < 15) return "Updated just now";
      if (seconds < 60) return `Updated ${seconds}s ago`;
      const minutes = Math.floor(seconds / 60);
      if (minutes < 60) return `Updated ${minutes}m ago`;
      return `Updated ${generatedAt.toLocaleString([], {
        month: "short",
        day: "numeric",
        hour: "2-digit",
        minute: "2-digit",
      })}`;
    }

    function section(title, candidates, kind) {
      const wrapper = document.createElement("section");
      const heading = document.createElement("h2");
      heading.textContent = title;
      wrapper.append(heading);
      if (!candidates.length) {
        const empty = document.createElement("div");
        empty.className = "subtle";
        empty.textContent = "None";
        wrapper.append(empty);
        return wrapper;
      }
      for (const candidate of candidates) wrapper.append(candidateNode(candidate, kind));
      return wrapper;
    }

    function candidateNode(candidate, kind) {
      const node = document.createElement("article");
      node.className = "candidate";
      node.dataset.kind = kind;

      const top = document.createElement("div");
      top.className = "candidate-top";
      const text = document.createElement("div");
      const statement = document.createElement("p");
      statement.className = "statement";
      statement.textContent = candidate.statement;
      text.append(statement);
      top.append(text);
      node.append(top);

      const actions = document.createElement("div");
      actions.className = "actions";
      if (candidate.recommended_action === "accept") {
        actions.append(actionButton("Accept", "primary", () => accept(candidate)));
        actions.append(actionButton("Edit", "", () => openEdit(candidate)));
        actions.append(actionButton("Reject", "danger", () => reject(candidate)));
      } else if (candidate.recommended_action === "reject") {
        actions.append(actionButton("Reject", "primary", () => reject(candidate)));
        actions.append(actionButton("Edit", "", () => openEdit(candidate)));
        actions.append(actionButton("Accept", "", () => accept(candidate)));
      } else {
        actions.append(actionButton("Review", "primary", () => openEdit(candidate)));
        actions.append(actionButton("Accept", "", () => accept(candidate)));
        actions.append(actionButton("Reject", "danger", () => reject(candidate)));
      }
      if (candidate.source_type === "working_state") {
        actions.append(actionButton("Snooze", "ghost", () => snooze(candidate)));
      }
      node.append(actions);

      const details = document.createElement("details");
      const summary = document.createElement("summary");
      summary.textContent = "Evidence";
      const evidence = document.createElement("pre");
      evidence.textContent = JSON.stringify(candidate.evidence || {}, null, 2);
      details.append(summary, evidence);
      node.append(details);
      return node;
    }

    function actionButton(label, className, handler) {
      const button = document.createElement("button");
      button.type = "button";
      button.className = className;
      button.textContent = label;
      button.addEventListener("click", handler);
      return button;
    }

    async function accept(candidate) {
      await postAction("/review/accept", { candidate_id: candidate.id });
      await loadCard();
    }

    async function reject(candidate) {
      const reason = window.prompt("Reason for rejection", "");
      if (reason === null) return;
      await postAction("/review/reject", { candidate_id: candidate.id, reason });
      await loadCard();
    }

    async function snooze(candidate) {
      await postAction("/review/snooze", { candidate_id: candidate.id, hours: 24 });
      await loadCard();
    }

    function openEdit(candidate) {
      state.activeCandidate = candidate;
      document.getElementById("editCandidateId").value = candidate.id;
      document.getElementById("editStatement").value = candidate.statement || "";
      document.getElementById("editReason").value = (candidate.evidence && candidate.evidence.summary) || "";
      document.getElementById("editScope").value = candidate.title || "";
      dialog.showModal();
    }

    async function postAction(path, payload) {
      const response = await fetch(path, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });
      if (!response.ok) {
        const error = await response.text();
        throw new Error(error);
      }
      return response.json();
    }

    loadCard();
  </script>
</body>
</html>
"""
