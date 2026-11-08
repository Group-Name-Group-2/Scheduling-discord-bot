document.addEventListener("DOMContentLoaded", () => {
    const list = document.getElementById("chat-messages");
    const form = document.getElementById("chat-form");
    const input = document.getElementById("chat-input");
    const sendButton = document.getElementById("chat-send");
    const errorText = document.getElementById("chat-error");

    if (!list || !form) {
        return;
    }

    const me = list.dataset.me;
    const other = list.dataset.other;
    // Group chats pass their own URLs; direct chats use the other person's ID.
    const isGroup = list.dataset.group === "true";
    const sendUrl = list.dataset.sendUrl || `/messages/${other}`;
    const pollUrl = list.dataset.pollUrl || `/api/messages/${other}`;
    let lastId = Number(list.dataset.lastId) || 0;

    const POLL_INTERVAL_MS = 4000;

    function formatTime(isoString) {
        const date = new Date(isoString);
        const now = new Date();
        const sameDay = date.toDateString() === now.toDateString();

        return sameDay
            ? date.toLocaleTimeString([], { hour: "numeric", minute: "2-digit" })
            : date.toLocaleString([], {
                month: "short",
                day: "numeric",
                hour: "numeric",
                minute: "2-digit",
            });
    }

    function scrollToBottom() {
        list.scrollTop = list.scrollHeight;
    }

    function appendMessage(message) {
        // Skip messages already on the page (send + poll can overlap).
        if (list.querySelector(`[data-message-id="${message.message_id}"]`)) {
            return;
        }

        document.getElementById("chat-empty")?.remove();

        // Group notices like "Alice added Bob".
        if (message.kind && message.kind !== "message") {
            const notice = document.createElement("p");
            notice.className = "chat-notice";
            notice.dataset.messageId = message.message_id;
            notice.textContent = message.text || "";
            list.append(notice);
            lastId = Math.max(lastId, message.message_id);
            return;
        }

        const fromMe = message.sender_steam_id === me;
        const wrapper = document.createElement("div");
        wrapper.className = "chat-message " + (fromMe ? "from-me" : "from-them");
        wrapper.dataset.messageId = message.message_id;

        if (isGroup && !fromMe) {
            const sender = document.createElement("span");
            sender.className = "chat-sender";
            sender.textContent = message.sender_name || "";
            wrapper.append(sender);
        }

        // textContent (not innerHTML) so message text can't inject HTML.
        const body = document.createElement("p");
        body.className = "chat-body";
        body.textContent = message.body;

        const time = document.createElement("time");
        time.className = "chat-time";
        time.dateTime = message.created_at;
        time.textContent = formatTime(message.created_at);

        wrapper.append(body, time);
        list.append(wrapper);

        lastId = Math.max(lastId, message.message_id);
    }

    async function readJson(response) {
        try {
            return await response.json();
        } catch {
            return { error: `Server error (${response.status}).` };
        }
    }

    // Format times rendered by the server.
    list.querySelectorAll("time.chat-time").forEach((el) => {
        el.textContent = formatTime(el.dateTime);
    });
    scrollToBottom();

    async function sendMessage() {
        const text = input.value.trim();

        if (!text) {
            return;
        }

        sendButton.disabled = true;
        errorText.textContent = "";

        try {
            const response = await fetch(sendUrl, {
                method: "POST",
                body: new URLSearchParams({ body: text }),
                headers: { "Accept": "application/json" },
            });
            const result = await readJson(response);

            if (!response.ok) {
                throw new Error(result.error || "Could not send the message.");
            }

            appendMessage(result.message);
            input.value = "";
            scrollToBottom();
        } catch (error) {
            errorText.textContent = error.message;
        } finally {
            sendButton.disabled = false;
            input.focus();
        }
    }

    form.addEventListener("submit", (event) => {
        event.preventDefault();
        sendMessage();
    });

    // Enter sends, Shift+Enter adds a new line.
    input.addEventListener("keydown", (event) => {
        if (event.key === "Enter" && !event.shiftKey) {
            event.preventDefault();
            sendMessage();
        }
    });

    async function poll() {
        try {
            const response = await fetch(
                `${pollUrl}?after=${lastId}`,
                { headers: { "Accept": "application/json" } }
            );

            if (!response.ok) {
                return;
            }

            const result = await response.json();
            const nearBottom =
                list.scrollHeight - list.scrollTop - list.clientHeight < 80;

            result.messages.forEach(appendMessage);

            if (result.messages.length && nearBottom) {
                scrollToBottom();
            }
        } catch {
            // Network hiccup: try again next interval.
        }
    }

    setInterval(() => {
        if (!document.hidden) {
            poll();
        }
    }, POLL_INTERVAL_MS);
});
