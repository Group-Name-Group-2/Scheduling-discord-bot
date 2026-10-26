document.addEventListener("DOMContentLoaded", () => {
    const form = document.getElementById("create-event-form");
    const message = document.getElementById("event-form-message");

    if (!form) {
        return;
    }

    // Default the date to today if no date was passed in.
    const dateInput = document.getElementById("event-date");

    if (!dateInput.value) {
        const today = new Date();
        const localDate = new Date(
            today.getTime() - today.getTimezoneOffset() * 60000
        ).toISOString().split("T")[0];

        dateInput.value = localDate;
    }

    form.addEventListener("submit", (event) => {
        event.preventDefault();

        message.textContent =
            "The form is working, but event creation has not been connected yet.";
    });
});