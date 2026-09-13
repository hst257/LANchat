const api = async (path, options = {}) => {
  const response = await fetch(path, {
    credentials: "same-origin",
    headers: { "Content-Type": "application/json", ...(options.headers || {}) },
    ...options,
  });
  if (response.status === 401 && location.pathname !== "/login") {
    location.href = "/login";
    throw new Error("Please sign in again");
  }
  if (!response.ok) {
    let detail = "Something went wrong";
    try {
      detail = (await response.json()).detail || detail;
    } catch (_) {}
    throw new Error(detail);
  }
  return response.status === 204 ? null : response.json();
};

const showNotice = (element, message, kind = "error") => {
  element.textContent = message;
  element.className = `notice ${kind}`;
  element.hidden = !message;
};

const formatTime = (iso) =>
  new Intl.DateTimeFormat(undefined, { hour: "numeric", minute: "2-digit" }).format(
    new Date(iso)
  );

const openSocket = (onEvent) => {
  const protocol = location.protocol === "https:" ? "wss:" : "ws:";
  let socket;
  let retryTimer;
  let closedByPage = false;

  const connect = () => {
    socket = new WebSocket(`${protocol}//${location.host}/ws`);
    socket.addEventListener("message", (event) => {
      try {
        onEvent(JSON.parse(event.data));
      } catch (_) {}
    });
    socket.addEventListener("close", (event) => {
      if (event.code === 4401) {
        location.href = "/login";
        return;
      }
      if (!closedByPage) retryTimer = setTimeout(connect, 1500);
    });
  };
  connect();

  window.addEventListener("beforeunload", () => {
    closedByPage = true;
    clearTimeout(retryTimer);
    socket?.close();
  });

  return {
    send(payload) {
      if (!socket || socket.readyState !== WebSocket.OPEN) return false;
      socket.send(JSON.stringify(payload));
      return true;
    },
    get ready() {
      return socket?.readyState === WebSocket.OPEN;
    },
  };
};

