document.addEventListener("DOMContentLoaded", () => {
    const gameCards = document.querySelectorAll(".game-card-clickable");
    const modal = document.getElementById("game-modal");

    if (!modal || gameCards.length === 0) {
        return;
    }

    const closeButton = document.getElementById("game-modal-close");
    const modalCover = document.getElementById("modal-game-cover");
    const modalTitle = document.getElementById("modal-game-title");
    const modalPlaytime = document.getElementById("modal-game-playtime");
    const steamLink = document.getElementById("modal-steam-link");
    const createEventButton = document.getElementById("modal-create-event");

    // We'll use this element to display the extra game information.
    const detailsContainer = document.getElementById("modal-game-details");

    async function openGameModal(card) {
        const appId = card.dataset.appid;
        const gameName = card.dataset.gameName;
        const playtime = card.dataset.playtime;

        modalTitle.textContent = gameName;
        modalPlaytime.textContent = `Your playtime: ${playtime} hours`;

        modalCover.src =
            `https://cdn.cloudflare.steamstatic.com/steam/apps/${appId}/header.jpg`;
        modalCover.alt = `${gameName} cover art`;
        modalCover.style.display = "";

        modalCover.onerror = () => {
            modalCover.style.display = "none";
        };

        steamLink.href = `https://store.steampowered.com/app/${appId}/`;

        // Show the modal immediately while the details load.
        detailsContainer.textContent = "Loading game information...";
        modal.showModal();

        try {
            const response = await fetch(`/api/games/${appId}`);

            if (!response.ok) {
                throw new Error("Game information is unavailable.");
            }

            const game = await response.json();

            // Don't show old results if another game was opened meanwhile.
            if (!modal.open || modalTitle.dataset.appid !== appId) {
                return;
            }

            modalTitle.textContent = game.name || gameName;

            const description = document.createElement("p");
            description.textContent =
                game.description || "No description is available.";

            const releaseDate = document.createElement("p");
            releaseDate.textContent =
                `Release date: ${game.release_date || "Unknown"}`;

            const developers = document.createElement("p");
            developers.textContent =
                `Developer: ${(game.developers || []).join(", ") || "Unknown"}`;

            const publishers = document.createElement("p");
            publishers.textContent =
                `Publisher: ${(game.publishers || []).join(", ") || "Unknown"}`;

            const genres = document.createElement("p");
            genres.textContent =
                `Genres: ${(game.genres || []).join(", ") || "Unknown"}`;

            detailsContainer.replaceChildren(
                description,
                releaseDate,
                developers,
                publishers,
                genres
            );
        } catch (error) {
            if (modal.open && modalTitle.dataset.appid === appId) {
                detailsContainer.textContent =
                    "Unable to load game information. Please try again later.";
            }

            console.error("Error loading game details:", error);
        }
    }

    gameCards.forEach((card) => {
        card.addEventListener("click", () => {
            const appId = card.dataset.appid;
            modalTitle.dataset.appid = appId;
            openGameModal(card);
        });

        card.addEventListener("keydown", (event) => {
            if (event.key === "Enter" || event.key === " ") {
                event.preventDefault();
                const appId = card.dataset.appid;
                modalTitle.dataset.appid = appId;
                openGameModal(card);
            }
        });
    });

    createEventButton.addEventListener("click", () => {
    const appId = modalTitle.dataset.appid;
    const gameName = modalTitle.textContent;

    const params = new URLSearchParams({
        game_id: appId,
        game_name: gameName
    });

    const friendIds = (modal.dataset.friendIds || "")
        .split(",")
        .filter(Boolean);

    friendIds.forEach((friendId) => {
        params.append("friend_id", friendId);
    });

    window.location.href = `/events/create?${params.toString()}`;
    });

    closeButton.addEventListener("click", () => {
        modal.close();
    });

    modal.addEventListener("click", (event) => {
        if (event.target === modal) {
            modal.close();
        }
    });
});