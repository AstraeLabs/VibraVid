(function () {
    "use strict";
    const config = document.currentScript.dataset;
    const confirmed = new WeakSet();
    let active = false;
    document.addEventListener("submit", async event => {
        const form = event.target;
        if (!(form instanceof HTMLFormElement) || event.defaultPrevented ||
            new URL(form.action, location.href).pathname !== config.watchlistUrl) return;
        if (confirmed.has(form)) { confirmed.delete(form); return; }
        const data = new FormData(form);
        let item;
        try { item = JSON.parse(data.get("item_payload")); } catch (_) { return; }
        if (["film", "movie", "ova"].includes((item.type || "").toLowerCase())) return;
        event.preventDefault();
        if (active) return;
        active = true;
        const dialog = document.createElement("dialog");
        dialog.style.cssText = "background:#17171f;color:#fff;border:1px solid #454550;border-radius:12px;padding:24px;width:min(440px,90vw)";
        dialog.setAttribute("aria-labelledby", "cn-watch-picker-title");
        dialog.innerHTML = '<h2 id="cn-watch-picker-title">Add to watchlist</h2><p class="watch-title"></p>' +
            '<label>What would you like to monitor?<select style="display:block;width:100%;margin:16px 0;padding:10px" aria-label="Seasons to add"><option value="all">Entire series, including new seasons</option></select></label>' +
            '<label>Quality<select class="watch-quality" style="display:block;width:100%;margin:16px 0;padding:10px" aria-label="Desired video quality"></select></label>' +
            '<p role="status">Loading available seasons...</p><div style="display:flex;justify-content:flex-end;gap:12px">' +
            '<button type="button" class="cn-mini watch-cancel">Cancel</button><button type="button" class="cn-mini watch-add" disabled>Add</button></div>';
        dialog.querySelector(".watch-title").textContent = item.name;
        const select = dialog.querySelector("select");
        const quality = dialog.querySelector(".watch-quality");
        const selectedQuality = data.get("quality") || "";
        quality.add(new Option("Use config", ""));
        const qualities = [...new Set(["1080p", "720p", "480p", selectedQuality])]
            .filter(value => /^\d+p$/.test(value) && parseInt(value) <= 1080 && value !== "360p")
            .sort((a, b) => parseInt(b) - parseInt(a));
        qualities.forEach(value => quality.add(new Option(value, value)));
        quality.value = qualities.includes(selectedQuality) ? selectedQuality : "";
        const status = dialog.querySelector('[role="status"]');
        const add = dialog.querySelector(".watch-add");
        const controller = new AbortController();
        const timeout = setTimeout(() => controller.abort(), 30000);
        dialog.addEventListener("close", () => {
            clearTimeout(timeout);
            controller.abort();
            dialog.remove();
            active = false;
            event.submitter?.focus();
        });
        dialog.querySelector(".watch-cancel").onclick = () => dialog.close();
        add.onclick = () => {
            let input = form.elements.namedItem("auto_season");
            if (!input) {
                input = document.createElement("input");
                input.type = "hidden"; input.name = "auto_season"; form.appendChild(input);
            }
            input.value = select.value;
            let qualityInput = form.elements.namedItem("quality");
            if (!qualityInput) {
                qualityInput = document.createElement("input");
                qualityInput.type = "hidden"; qualityInput.name = "quality"; form.appendChild(qualityInput);
            }
            qualityInput.value = quality.value;
            // The shared submit handler reads the page picker again on resubmit.
            const pagePicker = form.closest(".cn-tile")?.querySelector(".cn-quality-picker select") ||
                document.querySelector("#cn-series-quality .cn-quality-picker select");
            if (pagePicker) {
                if (![...pagePicker.options].some(option => option.value === quality.value)) {
                    pagePicker.add(new Option(quality.value, quality.value));
                }
                pagePicker.value = quality.value;
            }
            confirmed.add(form);
            dialog.close();
            form.requestSubmit();
        };
        document.body.appendChild(dialog);
        dialog.showModal();
        try {
            // Series details already contain the provider's actual season numbers.
            let numbers = [...document.querySelectorAll(".cn-stab[data-season]")].map(tab => tab.dataset.season);
            if (!numbers.length) {
                const response = await fetch(config.metadataUrl, {
                    method: "POST", body: data, signal: controller.signal
                });
                if (!response.ok) throw new Error("Unable to load seasons. Close and retry.");
                const metadata = await response.json();
                numbers = Object.keys(metadata.episodesPerSeason || {});
            }
            if (!dialog.open) return;
            [...new Set(numbers)].sort((a, b) => Number(a) - Number(b)).forEach(number => {
                select.add(new Option("Season " + number, number));
            });
            status.textContent = numbers.length ? "Downloads start when the selected quality is available." :
                "No seasons reported yet. You can monitor the entire series.";
            add.disabled = false;
        } catch (error) {
            if (dialog.open) status.textContent = error.name === "AbortError" ? "Loading timed out. Close and retry." : error.message;
        } finally { clearTimeout(timeout); }
    });
})();
