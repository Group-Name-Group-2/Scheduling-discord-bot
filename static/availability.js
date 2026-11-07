document.addEventListener("DOMContentLoaded", () => {
    const dataElement = document.getElementById("availability-data");
    const grid = document.getElementById("availability-grid");

    if (!dataElement || !grid) {
        return;
    }

    const weekends = JSON.parse(dataElement.textContent);
    const tabs = document.querySelectorAll(".weekend-tab");
    const cells = [...grid.querySelectorAll(".availability-cell")];
    const saveButton = document.getElementById("availability-save");
    const statusText = document.getElementById("availability-status");
    const summaryText = document.getElementById("availability-summary");
    const dayHeadings = [
        document.getElementById("day-0-heading"),
        document.getElementById("day-1-heading"),
    ];

    const PRESETS = {
        afternoon: [12, 13, 14, 15, 16, 17],
        evening: [18, 19, 20, 21, 22, 23],
    };

    // weekendId -> Set of "date|hour" strings (what's on screen).
    const state = new Map();
    // weekendId -> the same, as last saved on the server.
    const saved = new Map();

    for (const weekend of weekends) {
        const slots = new Set(weekend.slots.map(([d, h]) => `${d}|${h}`));
        state.set(weekend.id, slots);
        saved.set(weekend.id, new Set(slots));
    }

    let current = weekends[0];

    const key = (cell) =>
        `${current.days[Number(cell.dataset.day)].date}|${cell.dataset.hour}`;

    const isDirty = (id) => {
        const a = state.get(id);
        const b = saved.get(id);
        return a.size !== b.size || [...a].some((slot) => !b.has(slot));
    };

    function hoursLabel(count) {
        return `${count} hour${count === 1 ? "" : "s"}`;
    }

    function render() {
        const slots = state.get(current.id);

        current.days.forEach((day, i) => {
            dayHeadings[i].textContent = day.label;
        });

        for (const cell of cells) {
            const on = slots.has(key(cell));
            cell.classList.toggle("selected", on);
            cell.setAttribute("aria-pressed", on);
        }

        for (const tab of tabs) {
            const id = tab.dataset.weekend;
            const count = state.get(id).size;
            const selected = id === current.id;

            tab.setAttribute("aria-selected", selected);
            tab.classList.toggle("has-slots", count > 0);
            tab.querySelector(".weekend-tab-count").textContent =
                count ? hoursLabel(count) + (isDirty(id) ? " • unsaved" : "") : "Not set";
        }

        const perDay = current.days.map(
            (day) => [...slots].filter((s) => s.startsWith(day.date)).length
        );
        summaryText.textContent = slots.size
            ? `Saturday: ${hoursLabel(perDay[0])} · Sunday: ${hoursLabel(perDay[1])}`
            : "No hours selected for this weekend.";

        saveButton.disabled = !isDirty(current.id);
    }

    // ---- Click and drag to paint ----
    let painting = null; // true = selecting, false = clearing

    function paint(cell) {
        const slots = state.get(current.id);
        if (painting) {
            slots.add(key(cell));
        } else {
            slots.delete(key(cell));
        }
        statusText.textContent = "";
        render();
    }

    grid.addEventListener("pointerdown", (event) => {
        const cell = event.target.closest(".availability-cell");
        if (!cell) {
            return;
        }
        event.preventDefault();
        painting = !state.get(current.id).has(key(cell));
        paint(cell);
    });

    // elementFromPoint works for both mouse and touch drags.
    grid.addEventListener("pointermove", (event) => {
        if (painting === null) {
            return;
        }
        const target = document.elementFromPoint(event.clientX, event.clientY);
        const cell = target && target.closest(".availability-cell");
        if (cell && cell.classList.contains("selected") !== painting) {
            paint(cell);
        }
    });

    window.addEventListener("pointerup", () => {
        painting = null;
    });

    // Keyboard: Enter/Space toggles the focused hour.
    grid.addEventListener("keydown", (event) => {
        const cell = event.target.closest(".availability-cell");
        if (cell && (event.key === "Enter" || event.key === " ")) {
            event.preventDefault();
            painting = !state.get(current.id).has(key(cell));
            paint(cell);
            painting = null;
        }
    });

    // ---- Presets ----
    document.querySelectorAll("[data-preset]").forEach((button) => {
        button.addEventListener("click", () => {
            const preset = button.dataset.preset;
            const slots = state.get(current.id);

            if (preset === "clear") {
                slots.clear();
            } else {
                for (const cell of cells) {
                    const hour = Number(cell.dataset.hour);
                    if (preset === "all" || PRESETS[preset].includes(hour)) {
                        slots.add(key(cell));
                    }
                }
            }
            statusText.textContent = "";
            render();
        });
    });

    // ---- Weekend tabs ----
    tabs.forEach((tab) => {
        tab.addEventListener("click", () => {
            current = weekends.find((w) => w.id === tab.dataset.weekend);
            statusText.textContent = "";
            render();
        });
    });

    // ---- Save ----
    saveButton.addEventListener("click", async () => {
        const weekendId = current.id;
        const slots = [...state.get(weekendId)].map((s) => {
            const [date, hour] = s.split("|");
            return [date, Number(hour)];
        });

        saveButton.disabled = true;
        statusText.textContent = "Saving…";

        try {
            const response = await fetch("/api/availability", {
                method: "POST",
                headers: { "Content-Type": "application/json", "Accept": "application/json" },
                // `user` is only set when an admin edits someone else's schedule.
                body: JSON.stringify({ weekend: weekendId, slots, user: grid.dataset.user || null }),
            });

            let result = {};
            try {
                result = await response.json();
            } catch {
                result = { error: `Server error (${response.status}).` };
            }

            if (!response.ok) {
                throw new Error(result.error || "Could not save.");
            }

            saved.set(weekendId, new Set(state.get(weekendId)));
            statusText.textContent = "Saved ✓";
        } catch (error) {
            statusText.textContent = error.message;
        } finally {
            render();
        }
    });

    window.addEventListener("beforeunload", (event) => {
        if (weekends.some((w) => isDirty(w.id))) {
            event.preventDefault();
        }
    });

    render();
});
