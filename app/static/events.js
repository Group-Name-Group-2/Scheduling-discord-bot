document.addEventListener("DOMContentLoaded", () => {
    const calendarElement = document.getElementById("calendar");

    if (!calendarElement || typeof FullCalendar === "undefined") {
        return;
    }

    const modal = document.getElementById("event-details-modal");
    const closeButton = document.getElementById("close-event-modal");

    const modalTitle = document.getElementById("event-modal-title");
    const modalCover = document.getElementById("event-modal-cover");
    const modalDate = document.getElementById("event-modal-date");
    const modalTime = document.getElementById("event-modal-time");
    const modalDuration = document.getElementById("event-modal-duration");
    const modalCreator = document.getElementById("event-modal-creator");
    const modalParticipants = document.getElementById(
        "event-modal-participants"
    );

    if (
        !modal ||
        !closeButton ||
        !modalTitle ||
        !modalCover ||
        !modalDate ||
        !modalTime ||
        !modalDuration ||
        !modalCreator ||
        !modalParticipants
    ) {
        console.error("Event details modal elements are missing.");
        return;
    }

    function formatDuration(minutes) {
        const hours = Math.floor(minutes / 60);
        const remainingMinutes = minutes % 60;

        const parts = [];

        if (hours > 0) {
            parts.push(`${hours} ${hours === 1 ? "hour" : "hours"}`);
        }

        if (remainingMinutes > 0) {
            parts.push(
                `${remainingMinutes} ${
                    remainingMinutes === 1 ? "minute" : "minutes"
                }`
            );
        }

        return parts.join(" ") || "0 minutes";
    }

    function formatDate(dateString) {
        const [year, month, day] = dateString.split("-").map(Number);

        const date = new Date(year, month - 1, day);

        return new Intl.DateTimeFormat("en-US", {
            weekday: "long",
            year: "numeric",
            month: "long",
            day: "numeric"
        }).format(date);
    }

    function formatTime(dateString, timeString) {
        const [year, month, day] = dateString.split("-").map(Number);
        const [hours, minutes] = timeString.split(":").map(Number);

        const date = new Date(year, month - 1, day, hours, minutes);

        return new Intl.DateTimeFormat("en-US", {
            hour: "numeric",
            minute: "2-digit"
        }).format(date);
    }

    async function openEventModal(eventId) {
        modalTitle.textContent = "Loading event...";
        modalDate.textContent = "";
        modalTime.textContent = "";
        modalDuration.textContent = "";
        modalCreator.textContent = "";
        modalParticipants.replaceChildren();

        modalCover.hidden = true;
        modalCover.removeAttribute("src");

        if (!modal.open) {
            modal.showModal();
        }

        try {
            const response = await fetch(
                `/api/events/${encodeURIComponent(eventId)}`
            );

            const result = await response.json();

            if (!response.ok) {
                throw new Error(
                    result.error || "Unable to load event details."
                );
            }

            modalTitle.textContent = result.game_name;

            modalCover.src =
                `https://cdn.cloudflare.steamstatic.com/steam/apps/` +
                `${result.app_id}/header.jpg`;

            modalCover.alt = `${result.game_name} cover art`;
            modalCover.hidden = false;

            modalCover.onerror = () => {
                modalCover.hidden = true;
            };

            modalDate.textContent = formatDate(result.date);

            modalTime.textContent = formatTime(
                result.date,
                result.time
            );

            modalDuration.textContent = formatDuration(
                result.duration_minutes
            );

            modalCreator.textContent = result.creator.name;

            if (result.participants.length === 0) {
                const item = document.createElement("li");
                item.textContent = "No friends invited yet.";
                modalParticipants.appendChild(item);
            } else {
                result.participants.forEach((participant) => {
                    const item = document.createElement("li");

                    if (participant.avatar) {
                        const avatar = document.createElement("img");

                        avatar.src = participant.avatar;
                        avatar.alt = "";
                        avatar.width = 32;
                        avatar.height = 32;

                        item.appendChild(avatar);
                    }

                    const name = document.createElement("span");
                    name.textContent = participant.name;

                    item.appendChild(name);
                    modalParticipants.appendChild(item);
                });
            }
        } catch (error) {
            console.error("Failed to load event details:", error);

            modalTitle.textContent = "Unable to load event";
            modalDate.textContent = "";
            modalTime.textContent = "";
            modalDuration.textContent = "";
            modalCreator.textContent = "";

            const item = document.createElement("li");
            item.textContent =
                error.message || "Please try again later.";

            modalParticipants.replaceChildren(item);
        }
    }

    closeButton.addEventListener("click", () => {
        modal.close();
    });

    modal.addEventListener("click", (event) => {
        if (event.target === modal) {
            modal.close();
        }
    });

    const calendar = new FullCalendar.Calendar(calendarElement, {
        initialView: "dayGridMonth",
        height: "auto",
        fixedWeekCount: false,
        showNonCurrentDates: true,
        firstDay: 0,

        headerToolbar: {
            left: "prev,next today",
            center: "title",
            right: ""
        },

        buttonText: {
            today: "Today"
        },

        events: {
            url: "/api/events",
            failure() {
                console.error("Unable to load calendar events.");
            }
        },

        displayEventTime: true,

        eventTimeFormat: {
            hour: "numeric",
            minute: "2-digit",
            meridiem: "short"
        },

        eventColor: "#5865F2",
        eventTextColor: "#FFFFFF",

        eventClick(info) {
            info.jsEvent.preventDefault();
            openEventModal(info.event.id);
        },

        dateClick(info) {
            window.location.href =
                `/events/create?date=${encodeURIComponent(info.dateStr)}`;
        }
    });

    calendar.render();
});