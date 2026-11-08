document.addEventListener("DOMContentLoaded", () => {
    const form = document.getElementById("create-event-form");
    const message = document.getElementById("event-form-message");

    if (!form) {
        return;
    }

    const dateInput = document.getElementById("event-date");
    const submitButton = form.querySelector('button[type="submit"]');

    // Default the date to today if no date was passed in.
    if (!dateInput.value) {
        const today = new Date();

        const localDate = new Date(
            today.getTime() - today.getTimezoneOffset() * 60000
        ).toISOString().split("T")[0];

        dateInput.value = localDate;
    }

    form.addEventListener("submit", async (event) => {
        event.preventDefault();

        message.textContent = "Creating your game night...";
        submitButton.disabled = true;

        try {
            const formData = new FormData(form);
            const response = await fetch("/events/create", {
                method: "POST",
                body: new URLSearchParams(formData),
                headers: {
                    "Accept": "application/json"
                }
            });

            // The server may return plain text (e.g. "Internal Server
            // Error") if it crashes, so don't assume the body is JSON.
            let result = {};
            try {
                result = await response.json();
            } catch {
                result = {
                    error: `Server error (${response.status}). Please try again.`
                };
            }

            if (!response.ok) {
                throw new Error(
                    result.error || "Unable to create the event."
                );
            }

            message.textContent = result.message;

            // Prevent accidental duplicate submissions.
            form.reset();

            // Navigate to the calendar after a short delay.
            setTimeout(() => {
                window.location.href = "/events";
            }, 1200);

        } catch (error) {
            message.textContent =
                error.message || "Something went wrong. Please try again.";

        } finally {
            submitButton.disabled = false;
        }
    });
});

// ---------------------------------------------------------------------
// "Suggest a time": asks /api/events/recommend for the best date, time
// and game for the selected friends, then fills in the form.
// ---------------------------------------------------------------------
document.addEventListener("DOMContentLoaded", () => {
    const form = document.getElementById("create-event-form");
    const button = document.getElementById("suggest-button");
    const output = document.getElementById("suggest-result");

    if (!form || !button || !output) {
        return;
    }

    const dateInput = document.getElementById("event-date");
    const timeInput = document.getElementById("event-time");
    const gameSelect = document.getElementById("event-game");

    function el(tag, className, text) {
        const node = document.createElement(tag);
        if (className) {
            node.className = className;
        }
        if (text !== undefined) {
            node.textContent = text;
        }
        return node;
    }

    function flash(input) {
        input.classList.remove("just-filled");
        void input.offsetWidth; // restart the animation
        input.classList.add("just-filled");
    }

    function applyTime(date, time) {
        dateInput.value = date;
        timeInput.value = time;
        flash(dateInput);
        flash(timeInput);
    }

    function applyGame(appid) {
        if (gameSelect.querySelector(`option[value="${appid}"]`)) {
            gameSelect.value = String(appid);
            flash(gameSelect);
        }
    }

    function chip(label, onClick) {
        const chipButton = el("button", "suggest-chip", label);
        chipButton.type = "button";
        chipButton.addEventListener("click", onClick);
        return chipButton;
    }

    function showError(message) {
        output.replaceChildren(el("p", "suggest-error", message));
        output.hidden = false;
    }

    function showSuggestion(s) {
        const card = el("div", "suggest-card");

        if (s.game) {
            const cover = el("img", "suggest-cover");
            cover.src = `https://cdn.cloudflare.steamstatic.com/steam/apps/${s.game.appid}/header.jpg`;
            cover.alt = "";
            cover.onerror = () => cover.remove();
            card.append(cover);
        }

        const body = el("div", "suggest-card-body");
        body.append(
            el("p", "suggest-eyebrow", "Best fit · filled in below"),
            el("p", "suggest-when", `${s.date_label}, ${s.time_label}`),
            el("p", "suggest-game", s.game ? s.game.name : "No game everyone owns was found; pick one below."),
        );

        const reasons = el("ul", "suggest-reasons");
        for (const reason of s.reasons) {
            reasons.append(el("li", null, reason));
        }
        body.append(reasons);
        card.append(body);

        const extras = el("div", "suggest-extras");

        if (s.alternative_times.length) {
            const row = el("div", "suggest-chip-row");
            row.append(el("span", "suggest-chip-label", "Other times:"));
            for (const alt of s.alternative_times) {
                row.append(chip(
                    `${alt.label} (${alt.attending}/${s.group_size})`,
                    () => applyTime(alt.date, alt.time),
                ));
            }
            extras.append(row);
        }

        if (s.alternative_games.length) {
            const row = el("div", "suggest-chip-row");
            row.append(el("span", "suggest-chip-label", "Other games:"));
            for (const game of s.alternative_games) {
                row.append(chip(game.name, () => applyGame(game.appid)));
            }
            extras.append(row);
        }

        output.replaceChildren(card, extras);
        output.hidden = false;

        applyTime(s.date, s.time);
        if (s.game) {
            applyGame(s.game.appid);
        }
    }

    button.addEventListener("click", async () => {
        const data = new URLSearchParams();
        form.querySelectorAll('input[name="friend_ids"]:checked').forEach((box) => {
            data.append("friend_ids", box.value);
        });
        data.append("duration", document.getElementById("event-duration").value);

        if (!data.has("friend_ids")) {
            showError("Select at least one friend above first.");
            return;
        }

        button.disabled = true;
        button.classList.add("loading");
        output.hidden = false;
        output.replaceChildren(el("p", "muted-text", "Comparing schedules and libraries…"));

        try {
            const response = await fetch("/api/events/recommend", {
                method: "POST",
                body: data,
                headers: { "Accept": "application/json" },
            });

            let result = {};
            try {
                result = await response.json();
            } catch {
                result = { error: `Server error (${response.status}). Please try again.` };
            }

            if (!response.ok) {
                throw new Error(result.error || "Couldn't make a suggestion.");
            }

            showSuggestion(result);
        } catch (error) {
            showError(error.message);
        } finally {
            button.disabled = false;
            button.classList.remove("loading");
        }
    });
});
