document.addEventListener("DOMContentLoaded", () => {
    const calendarElement = document.getElementById("calendar");

    if (!calendarElement || typeof FullCalendar === "undefined") {
        return;
    }

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

    dateClick(info) {
        window.location.href =
            `/events/create?date=${encodeURIComponent(info.dateStr)}`;
    }
});

    calendar.render();
});