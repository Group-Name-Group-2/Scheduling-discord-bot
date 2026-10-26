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

            const result = await response.json();

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