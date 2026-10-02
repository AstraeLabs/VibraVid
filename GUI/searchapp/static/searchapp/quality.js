(function () {
    "use strict";
    const config = document.currentScript.dataset;
    const targets = ["1080p", "720p", "480p"];
    const shared = document.querySelector("#cn-series-quality .cn-quality-picker");
    function field(form, name, value) {
        let input = form.elements.namedItem(name);
        if (!input) { input = document.createElement("input"); input.type = "hidden"; input.name = name; form.appendChild(input); }
        input.value = value;
        input.disabled = false;
    }
    function sourceForm(picker) {
        return picker.closest(".cn-tile")?.querySelector("form") ||
            document.querySelector(".cn-series-actions form");
    }
    async function request(form) {
        const data = new FormData(form);
        const response = await fetch(config.qualityUrl, {
            signal: AbortSignal.timeout(30000),
            method: "POST", headers: {"Content-Type": "application/json", "X-CSRFToken": data.get("csrfmiddlewaretoken")},
            body: JSON.stringify({source_alias: data.get("source_alias"), item_payload: data.get("item_payload"),
                season: data.get("season_number") || data.get("selected_seasons") || data.get("season") || data.get("auto_season") ||
                    document.querySelector(".cn-stab.is-active")?.dataset.season,
                episode: data.get("selected_episodes") || data.get("episode")})
        });
        const result = await response.json();
        if (!response.ok) throw new Error(result.message || "Unable to check provider qualities.");
        return result;
    }
    function populate(picker, available) {
        const select = picker.querySelector("select"), selected = select.value;
        select.replaceChildren(new Option("Use config", ""));
        const qualities = [...new Set([...targets, selected, ...(available || [])])].filter(q => /^\d+p$/.test(q) && parseInt(q) <= 1080 && q !== "360p");
        qualities.sort((a, b) => parseInt(b) - parseInt(a)).forEach(q => {
            const suffix = available === null ? " - check availability" : available.includes(q) ? " - available" : " - Watchlist";
            select.add(new Option(q + suffix, q));
        });
        select.value = selected;
    }
    async function load(picker, form) {
        const status = picker.querySelector(".cn-quality-result"), button = picker.querySelector("button");
        const generation = (picker._generation || 0) + 1;
        picker._generation = generation;
        button.disabled = true; status.textContent = "Checking the provider...";
        try {
            const result = await request(form || sourceForm(picker));
            if (picker._generation !== generation) return null;
            if (document.activeElement === picker.querySelector("select")) {
                picker._pendingQualities = result.qualities || [];
            } else {
                delete picker._pendingQualities;
                populate(picker, result.qualities || []);
            }
            status.textContent = (result.sample ? result.sample + ": " : "") +
                (result.qualities?.length ? "Available: " + result.qualities.join(", ") + ". " : "") +
                (result.message || "Other qualities: add to Watchlist and wait for availability.");
            return result;
        } catch (error) {
            if (picker._generation === generation) status.textContent = error.name === "TimeoutError"
                ? "Provider check timed out. Retry, use config, or add to Watchlist."
                : error.message;
            return null;
        } finally {
            if (picker._generation === generation) button.disabled = false;
        }
    }
    function key(form) {
        const data = new FormData(form);
        return "cn-quality:" + data.get("source_alias") + ":" + data.get("item_payload");
    }
    document.querySelectorAll(".cn-quality-picker").forEach(picker => {
        populate(picker, null);
        const form = sourceForm(picker);
        if (shared === picker && form) {
            try { picker.querySelector("select").value = sessionStorage.getItem(key(form)) || ""; } catch (_) {}
        }
        picker.querySelector("button").addEventListener("click", () => load(picker));
        picker.querySelector("select").addEventListener("change", () => {
            if (!picker._generation) load(picker);
        });
        picker.querySelector("select").addEventListener("blur", () => {
            if (picker._pendingQualities !== undefined) {
                populate(picker, picker._pendingQualities);
                delete picker._pendingQualities;
            }
        });
    });
    document.addEventListener("submit", async function (event) {
        if (event.defaultPrevented) return;
        const form = event.target;
        if (!(form instanceof HTMLFormElement)) return;
        if (form.dataset.qualityPending) { event.preventDefault(); return; }
        const picker = form.closest(".cn-tile")?.querySelector(".cn-quality-picker") || shared;
        if (!picker) return;
        const data = new FormData(form);
        if (!data.has("item_payload")) return;
        const quality = picker.querySelector("select").value;
        field(form, "quality", quality);
        const action = new URL(form.action, location.href).pathname;
        const watchlist = action === config.watchlistUrl;
        if (watchlist) { field(form, "auto_enabled", "on"); return; }
        const download = action === config.downloadUrl || data.has("download_type");
        if (!download) {
            try { sessionStorage.setItem(key(form), quality); } catch (_) {}
            return;
        }
        if (!quality) return;
        event.preventDefault();
        form.dataset.qualityPending = "1";
        const submitter = event.submitter;
        const previousLabel = submitter?.textContent;
        const wasDisabled = submitter?.disabled;
        if (submitter) { submitter.disabled = true; submitter.textContent = "Checking quality..."; }
        try {
            const result = await load(picker, form);
            if (!result) return;
            if (!result.qualities?.includes(quality)) {
                picker.querySelector(".cn-quality-result").textContent =
                    "Selected quality is unavailable or could not be verified for this video. Add to Watchlist to wait, or use config.";
                return;
            }
            field(form, "quality", quality);
            HTMLFormElement.prototype.submit.call(form);
        } finally {
            delete form.dataset.qualityPending;
            if (submitter) { submitter.disabled = wasDisabled; submitter.textContent = previousLabel; }
        }
    });
    document.querySelectorAll(".js-check-qualities").forEach(button => button.addEventListener("click", async function () {
        const form = button.closest("form"), status = form.querySelector(".cn-quality-result");
        button.disabled = true; status.textContent = "Checking...";
        try {
            const result = await request(form);
            status.textContent = (result.sample ? result.sample + ": " : "") +
                (result.qualities.length ? result.qualities.join(", ") : result.message || "No qualities reported.");
        } catch (error) { status.textContent = error.message; }
        finally { button.disabled = false; }
    }));
})();
