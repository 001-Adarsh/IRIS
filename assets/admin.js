const loginView = document.getElementById("login-view");
const inboxView = document.getElementById("inbox-view");
const inboxError = document.getElementById("inbox-error");
const loginForm = document.getElementById("login-form");
const loginError = document.getElementById("login-error");
const requestList = document.getElementById("request-list");
const transcript = document.getElementById("transcript");
const conversationTitle = document.getElementById("conversation-title");
const conversationMeta = document.getElementById("conversation-meta");
const conversationActions = document.getElementById("conversation-actions");
const replyForm = document.getElementById("reply-form");
const replyInput = document.getElementById("reply");
let currentFilter = "pending";
let selectedId = null;
let lastMessageId = 0;
let refreshTimer = null;
let presenceTimer = null;
let refreshInProgress = false;

async function api(path, options = {}) {
  let response;
  try {
    response = await fetch(path, {
      credentials: "same-origin",
      ...options,
      headers: {
        ...(options.body ? { "Content-Type": "application/json" } : {}),
        ...options.headers,
      },
    });
  } catch (error) {
    if (error instanceof TypeError) {
      throw new Error("Cannot reach IRIS right now. Check your connection, then try Refresh.");
    }
    throw error;
  }
  if (response.status === 204) return null;
  const result = await response.json().catch(() => ({}));
  if (!response.ok) {
    const error = new Error(result.detail || `Request failed (${response.status})`);
    error.status = response.status;
    throw error;
  }
  return result;
}

function showLogin(message = "") {
  selectedId = null;
  lastMessageId = 0;
  replyForm.hidden = true;
  conversationActions.replaceChildren();
  conversationTitle.textContent = "Choose a request";
  conversationMeta.textContent = "";
  transcript.innerHTML = '<p class="empty">Requests and approved conversations will appear here.</p>';
  loginView.hidden = false;
  inboxView.hidden = true;
  inboxError.hidden = true;
  loginError.textContent = message;
  window.clearInterval(refreshTimer);
  refreshTimer = null;
  window.clearInterval(presenceTimer);
  presenceTimer = null;
}

function showInbox() {
  loginView.hidden = true;
  inboxView.hidden = false;
  loginError.textContent = "";
  inboxError.hidden = true;
  inboxError.textContent = "";
}

async function sendPresenceHeartbeat() {
  try {
    await api("/v1/admin/presence", { method: "POST" });
  } catch (error) {
    showError(error);
  }
}

function startPresenceHeartbeat() {
  window.clearInterval(presenceTimer);
  sendPresenceHeartbeat();
  presenceTimer = window.setInterval(sendPresenceHeartbeat, 20000);
}

function addBubble(message) {
  const bubble = document.createElement("article");
  bubble.className = `bubble ${message.sender === "admin" ? "admin" : ""}`;
  const sender = document.createElement("small");
  sender.textContent = message.sender === "admin" ? "You" : "Visitor";
  const content = document.createElement("div");
  content.textContent = message.content;
  bubble.append(sender, content);
  transcript.appendChild(bubble);
  transcript.scrollTop = transcript.scrollHeight;
}

function actionButton(label, action, style = "secondary") {
  const button = document.createElement("button");
  button.className = style;
  button.type = "button";
  button.textContent = label;
  button.addEventListener("click", action);
  conversationActions.appendChild(button);
}

async function selectConnection(connection) {
  selectedId = connection.id;
  lastMessageId = 0;
  transcript.replaceChildren();
  conversationTitle.textContent = connection.display_name || `Visitor ${connection.id}`;
  conversationMeta.textContent = `${connection.status} · ${new Date(connection.created_at).toLocaleString()}`;
  conversationActions.replaceChildren();
  replyForm.hidden = connection.status !== "approved";

  if (connection.status === "pending") {
    actionButton("Approve", () => decide("approve"), "primary");
    actionButton("Decline", () => decide("decline"), "danger");
  } else if (connection.status === "approved") {
    actionButton("Close chat", () => decide("close"), "secondary");
  }
  actionButton("Delete", deleteSelected, "danger");
  await refreshMessages();
}

async function refreshMessages() {
  if (selectedId === null) return;
  const messages = await api(
    `/v1/admin/connections/${selectedId}/messages?after_id=${lastMessageId}`,
  );
  if (lastMessageId === 0 && messages.length === 0) {
    const empty = document.createElement("p");
    empty.className = "empty";
    empty.textContent = "No messages yet.";
    transcript.appendChild(empty);
  }
  for (const message of messages) {
    transcript.querySelector(".empty")?.remove();
    addBubble(message);
    lastMessageId = message.id;
  }
}

async function loadConnections() {
  const status = currentFilter === "all" ? "" : `?status=${currentFilter}`;
  const connections = await api(`/v1/admin/connections${status}`);
  requestList.replaceChildren();
  if (connections.length === 0) {
    const empty = document.createElement("p");
    empty.className = "empty";
    empty.textContent = currentFilter === "pending" ? "No new requests." : "No conversations found.";
    requestList.appendChild(empty);
    if (selectedId === null) {
      transcript.innerHTML = '<p class="empty">Choose a request to view its conversation.</p>';
    }
    return;
  }
  for (const connection of connections) {
    const item = document.createElement("button");
    item.className = `request${connection.id === selectedId ? " selected" : ""}`;
    item.type = "button";
    const name = document.createElement("strong");
    name.textContent = connection.display_name || `Visitor ${connection.id}`;
    const date = document.createElement("small");
    date.textContent = `${connection.status} · ${new Date(connection.updated_at).toLocaleString()}`;
    const preview = document.createElement("p");
    preview.textContent = connection.last_message || "No messages";
    item.append(name, date, preview);
    item.addEventListener("click", () => selectConnection(connection).catch(showError));
    requestList.appendChild(item);
  }
}

async function refreshInbox() {
  if (refreshInProgress || inboxView.hidden) return;
  refreshInProgress = true;
  try {
    await loadConnections();
    await refreshMessages();
    inboxError.hidden = true;
    inboxError.textContent = "";
  } catch (error) {
    showError(error);
  } finally {
    refreshInProgress = false;
  }
}

async function decide(action) {
  if (selectedId === null) return;
  await api(`/v1/admin/connections/${selectedId}/decision`, {
    method: "POST",
    body: JSON.stringify({ action }),
  });
  await loadConnections();
  const refreshed = await api("/v1/admin/connections");
  const connection = refreshed.find((item) => item.id === selectedId);
  if (connection) await selectConnection(connection);
}

async function deleteSelected() {
  if (selectedId === null || !window.confirm("Permanently delete this conversation and its messages?")) return;
  await api(`/v1/admin/connections/${selectedId}`, { method: "DELETE" });
  selectedId = null;
  lastMessageId = 0;
  replyForm.hidden = true;
  conversationActions.replaceChildren();
  conversationTitle.textContent = "Choose a request";
  conversationMeta.textContent = "";
  transcript.innerHTML = '<p class="empty">Choose a request to view its conversation.</p>';
  await loadConnections();
}

function showError(error) {
  if (error.status === 401) {
    showLogin("Your admin session expired. Please sign in again.");
  } else {
    inboxError.textContent = error.message || "Could not load the inbox. Try Refresh.";
    inboxError.hidden = false;
  }
}

loginForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  const passwordInput = document.getElementById("admin-password");
  const button = loginForm.querySelector("button");
  button.disabled = true;
  loginError.textContent = "";
  try {
    await api("/v1/admin/session", {
      method: "POST",
      body: JSON.stringify({ password: passwordInput.value }),
    });
    passwordInput.value = "";
    showInbox();
    await refreshInbox();
    refreshTimer = window.setInterval(refreshInbox, 5000);
    startPresenceHeartbeat();
  } catch (error) {
    loginError.textContent = error.message;
  } finally {
    button.disabled = false;
  }
});

document.querySelectorAll(".tab").forEach((tab) => {
  tab.addEventListener("click", () => {
    document.querySelector(".tab.active")?.classList.remove("active");
    tab.classList.add("active");
    currentFilter = tab.dataset.status;
    refreshInbox();
  });
});

document.getElementById("refresh").addEventListener("click", () => {
  refreshInbox();
});

document.getElementById("sign-out").addEventListener("click", async () => {
  try {
    await api("/v1/admin/session", { method: "DELETE" });
    selectedId = null;
    showLogin();
  } catch (error) {
    showError(error);
  }
});

replyForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  const message = replyInput.value.trim();
  if (!message || selectedId === null) return;
  const button = replyForm.querySelector("button");
  button.disabled = true;
  try {
    await api(`/v1/admin/connections/${selectedId}/messages`, {
      method: "POST",
      body: JSON.stringify({ message }),
    });
    replyInput.value = "";
    await refreshInbox();
    startPresenceHeartbeat();
  } catch (error) {
    showError(error);
  } finally {
    button.disabled = false;
  }
});

api("/v1/admin/connections")
  .then(() => {
    showInbox();
    return refreshInbox();
  })
  .then(() => {
    refreshTimer = window.setInterval(refreshInbox, 5000);
  })
  .catch((error) => {
    showLogin(error.status === 401 ? "" : error.message);
  });
