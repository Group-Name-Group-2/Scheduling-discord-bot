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

        dateClick(info) {
            window.location.href =
                `/events/create?date=${encodeURIComponent(info.dateStr)}`;
        }
    });

    calendar.render();
});