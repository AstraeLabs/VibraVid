(function () {
    "use strict";
    const node = document.getElementById("cn-audio-config");
    if (!node) return;
    const config = JSON.parse(node.textContent || "{}");
    const languages = config.languages || [];
    const allowed = new Set(["all", ...languages.map(language => language.code)]);
    const storageKey = "cn-audio:" + languages.map(language => language.code).join("|");
    const dialog = document.getElementById("cn-movie-audio-dialog");
    const confirmed = new WeakSet();
    let movieForm = null;
    let movieSubmitter = null;
    let selected = config.selection || "all";
    if (!config.explicit) {
        try { selected = sessionStorage.getItem(storageKey) || selected; } catch (_) {}
    }
    if (!allowed.has(selected)) selected = "all";

    function codes(value) {
        return String(value || "").toLowerCase().split(/[,|\s/]+/).filter(Boolean);
    }
    function matches(available, wanted) {
        return available === wanted || (!wanted.includes("-") && available.split("-")[0] === wanted);
    }
    function availableForSelection(available, choice) {
        const wanted = choice === "all" ? languages.map(language => language.code.toLowerCase()) : [choice.toLowerCase()];
        return (available || []).some(language => !wanted.length || wanted.some(code => matches(language.code.toLowerCase(), code)));
    }
    function eligible(element) {
        const available = codes(element.dataset.verifiedLanguages || element.dataset.languages);
        if (!available.length) return selected === "all";
        const wanted = selected === "all" ? languages.map(language => language.code.toLowerCase()) : [selected.toLowerCase()];
        return !wanted.length || wanted.some(language => available.some(code => matches(code, language)));
    }
    function pickerFor(qualityPicker) {
        return dialog?.open && movieForm?.closest(".cn-tile") === qualityPicker.closest(".cn-tile")
            ? dialog.querySelector(".cn-audio-picker") : null;
    }
    function selectionFor(form) {
        if (!form?.elements.namedItem("item_payload")) return null;
        const tile = form.closest(".cn-tile");
        if (tile ? !["movie", "series", "anime"].includes(tile.dataset.mediaKind)
                 : !document.getElementById("cn-series-quality")) return null;
        return form.hasAttribute("data-audio-prompt") ? form.elements.namedItem("audio_language")?.value || selected : selected;
    }
    function field(form, name, value) {
        let input = form.elements.namedItem(name);
        if (!input) {
            input = document.createElement("input"); input.type = "hidden"; input.name = name; form.appendChild(input);
        }
        input.value = value;
    }
    function prepareForm(form) {
        if (!(form instanceof HTMLFormElement)) return;
        const choice = selectionFor(form);
        if (!choice) return;
        field(form, "audio_language", choice);
        const input = form.elements.namedItem("item_payload");
        if (input) {
            try {
                const payload = JSON.parse(input.value);
                payload._audio_language = choice;
                input.value = JSON.stringify(payload);
            } catch (_) {}
        }
    }
    function applyFilter() {
        document.querySelectorAll("[data-audio-language]").forEach(button => {
            button.setAttribute("aria-pressed", String(button.dataset.audioLanguage === selected));
        });
        document.querySelectorAll('form input[name="audio_language"]').forEach(input => {
            if (!input.form.elements.namedItem("item_payload")) input.value = selected;
        });
        let shown = 0;
        const kind = document.querySelector('#cn-kinds [aria-pressed="true"]')?.dataset.kind;
        document.querySelectorAll("#cn-results .cn-tile").forEach(tile => {
            tile.hidden = !!((kind && tile.dataset.kind !== kind) || !eligible(tile));
            if (!tile.hidden) shown++;
        });
        const count = document.getElementById("cn-count");
        if (count) count.textContent = shown;
        const empty = document.getElementById("cn-nokind");
        if (empty) empty.hidden = shown > 0;

        let episodeCount = 0;
        document.querySelectorAll(".cn-spanel").forEach(panel => {
            let count = 0;
            panel.querySelectorAll(".cn-ep").forEach(episode => {
                const show = eligible(episode);
                episode.hidden = !show;
                const checkbox = episode.querySelector(".cn-ep-pick");
                if (checkbox) { checkbox.disabled = !show; if (!show) checkbox.checked = false; }
                if (show) count++;
            });
            episodeCount += count;
            const number = panel.dataset.season;
            document.querySelectorAll("[data-language-count]").forEach(label => {
                if (label.dataset.languageCount === number) label.textContent = count;
            });
            const tab = [...document.querySelectorAll(".cn-stab")].find(tab => tab.dataset.season === number);
            if (tab) {
                tab.hidden = count === 0;
                const checkbox = tab.querySelector(".cn-season-pick");
                if (checkbox) { checkbox.disabled = !count; if (!count) checkbox.checked = false; }
            }
            panel.querySelectorAll('button[type="submit"]').forEach(button => { button.disabled = !count; });
        });
        const noEpisodes = document.querySelector(".cn-language-empty");
        if (noEpisodes) noEpisodes.hidden = episodeCount > 0;
        const all = document.getElementById("cn-all");
        if (all) all.disabled = !episodeCount;
        document.dispatchEvent(new CustomEvent("cn:audio-filter"));
    }
    function setFilter(value) {
        if (!allowed.has(value)) return;
        selected = value;
        try { sessionStorage.setItem(storageKey, value); } catch (_) {}
        document.querySelectorAll('[data-audio-prompt] input[name="audio_language"]').forEach(input => { input.value = value; });
        document.querySelectorAll(".cn-quality-picker").forEach(picker => {
            picker.dispatchEvent(new CustomEvent("cn:audio-change", {bubbles: true}));
        });
        applyFilter();
    }
    function applyAvailable(qualityPicker, result) {
        const picker = pickerFor(qualityPicker);
        if (!picker) return;
        const status = picker.querySelector(".cn-audio-result");
        const select = picker.querySelector("select");
        select.disabled = false;
        dialog.querySelector("[data-audio-download]").disabled = false;
        status.classList.remove("is-error");
        if (!result.audio_supported) {
            status.textContent = "This provider checks audio when the video is prepared. Missing selected audio stops that video.";
            return;
        }
        const available = (result.audio_languages || []).filter(language => !languages.length ||
            languages.some(configured => matches(language.code.toLowerCase(), configured.code.toLowerCase())));
        if (!languages.length) {
            available.forEach(language => {
                if (![...select.options].some(option => option.value === language.code))
                    select.add(new Option(language.name, language.code));
            });
        }
        [...select.options].forEach(option => {
            option.disabled = !availableForSelection(available, option.value);
            option.dataset.label ||= option.textContent;
            option.textContent = option.dataset.label + (option.disabled ? " — unavailable" : "");
        });
        dialog.querySelector("[data-audio-download]").disabled = !!select.selectedOptions[0]?.disabled;
        const label = available.some(language => language.source === "catalog") ? "Source audio: " : "Verified audio: ";
        status.textContent = available.length ? label + available.map(language => language.name).join(", ") :
            "No labelled audio tracks found. Choose tracks manually to inspect this video.";
        const tile = qualityPicker.closest(".cn-tile");
        if (tile && available.length) tile.dataset.verifiedLanguages = available.map(language => language.code).join(",");
        applyFilter();
    }
    window.CinemaAudio = {selectionFor, prepareForm, pickerFor, applyAvailable, availableForSelection};
    document.addEventListener("submit", event => {
        const form = event.target;
        prepareForm(form);
        if (!dialog || event.defaultPrevented || !form.matches("[data-audio-prompt]")) return;
        if (confirmed.delete(form) || form.closest(".cn-tile").querySelector(".cn-manual-tracks")?.checked) return;
        event.preventDefault();
        event.stopImmediatePropagation();
        if (dialog.open) return;
        movieForm = form;
        movieSubmitter = event.submitter;
        const select = dialog.querySelector("select");
        [...select.options].forEach(option => {
            option.disabled = false;
            option.textContent = option.dataset.label || option.textContent;
        });
        select.value = selectionFor(form);
        select.disabled = true;
        const status = dialog.querySelector(".cn-audio-result");
        status.classList.remove("is-error");
        status.textContent = "Checking available audio...";
        dialog.querySelector("[data-audio-download]").disabled = true;
        dialog.querySelector("#cn-audio-dialog-movie").textContent = form.closest(".cn-tile").dataset.name;
        dialog.showModal();
        form.closest(".cn-tile").querySelector(".cn-quality-check").click();
    }, true);
    if (dialog) {
        dialog.querySelector("[data-audio-cancel]").addEventListener("click", () => dialog.close());
        dialog.addEventListener("click", event => { if (event.target === dialog) dialog.close(); });
        dialog.addEventListener("close", () => {
            const quality = movieForm?.closest(".cn-tile").querySelector(".cn-quality-picker");
            if (quality) {
                quality._generation = (quality._generation || 0) + 1;
                quality.querySelector(".cn-quality-check").disabled = false;
            }
            movieForm = movieSubmitter = null;
        });
        dialog.querySelector("select").addEventListener("change", event => {
            field(movieForm, "audio_language", event.target.value);
            dialog.querySelector(".cn-audio-result").classList.remove("is-error");
            dialog.querySelector("[data-audio-download]").disabled = !!event.target.selectedOptions[0]?.disabled;
        });
        dialog.querySelector("[data-audio-download]").addEventListener("click", () => {
            field(movieForm, "audio_language", dialog.querySelector("select").value);
            dialog.querySelector("select").disabled = true;
            dialog.querySelector("[data-audio-download]").disabled = true;
            confirmed.add(movieForm);
            if (movieSubmitter) movieForm.requestSubmit(movieSubmitter);
            else movieForm.requestSubmit();
        });
    }
    document.addEventListener("DOMContentLoaded", function () {
        document.querySelectorAll("[data-language-filter]").forEach(filter => filter.addEventListener("click", event => {
            const button = event.target.closest("[data-audio-language]");
            if (button) setFilter(button.dataset.audioLanguage);
        }));
        document.getElementById("cn-kinds")?.addEventListener("click", applyFilter);
        applyFilter();
    });
})();
