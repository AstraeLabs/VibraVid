(function () {
    "use strict";
    const config = document.currentScript.dataset;
    const DAYS_PER_PAGE = 14, PAGES = 6, MINUTE_STEP = 5, REFRESH_MS = 15000;
    const CLOCK = '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4" ' +
        'stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/></svg>';

    const sheet = document.createElement("link");
    sheet.rel = "stylesheet"; sheet.href = config.css;
    document.head.appendChild(sheet);

    function make(tag, className, text) {
        const node = document.createElement(tag);
        if (className) node.className = className;
        if (text != null) node.textContent = text;
        if (tag === "button") node.type = "button";
        return node;
    }
    const pad = n => String(n).padStart(2, "0");
    const startOfDay = date => new Date(date.getFullYear(), date.getMonth(), date.getDate());
    const addDays = (date, n) => new Date(date.getFullYear(), date.getMonth(), date.getDate() + n);
    const format = (date, options) => date.toLocaleDateString("en-GB", options);

    function dayLabel(date) {
        const diff = Math.round((startOfDay(date) - startOfDay(new Date())) / 86400000);
        if (diff === 0) return "Today";
        if (diff === 1) return "Tomorrow";
        return format(date, {weekday: "short", day: "numeric", month: "short"});
    }
    function whenLabel(date) {
        return dayLabel(date) + " · " + pad(date.getHours()) + ":" + pad(date.getMinutes());
    }
    function distance(date) {
        const minutes = Math.round((date - Date.now()) / 60000);
        if (minutes < 1) return "now";
        const d = Math.floor(minutes / 1440), h = Math.floor((minutes % 1440) / 60), m = minutes % 60;
        return "in " + [d && d + " d", h && h + " h", !d && m && m + " min"].filter(Boolean).join(" ");
    }

    let toastTimer = null;
    function toast(text, options) {
        document.querySelector(".cn-sch-toast")?.remove();
        clearTimeout(toastTimer);
        const node = make("div", "cn-sch-toast" + (options?.bad ? " is-bad" : ""));
        node.setAttribute("role", "status");
        node.appendChild(make("span", "", text));
        if (options?.link) {
            const link = make("a", "", "View");
            link.href = options.link;
            node.appendChild(link);
        }
        document.body.appendChild(node);
        toastTimer = setTimeout(() => node.remove(), 6000);
    }

    async function post(url, data) {
        const response = await fetch(url, {method: "POST", headers: {"X-CSRFToken": config.csrf}, body: data});
        const result = await response.json().catch(() => ({}));
        if (!response.ok) throw new Error(result.message || "Request failed.");
        return result;
    }

    // ── Day / time picker ───────────────────────────────────────────────────
    // Resolves with the chosen Date, or null when dismissed.
    function pickTime(title, what, confirmLabel, initial) {
        return new Promise(resolve => {
            const soon = new Date(Date.now() + 3600000);
            soon.setMinutes(Math.ceil(soon.getMinutes() / MINUTE_STEP) * MINUTE_STEP, 0, 0);
            const start = initial && initial > Date.now() ? initial : soon;
            const state = {
                day: startOfDay(start), hour: start.getHours(),
                minute: Math.floor(start.getMinutes() / MINUTE_STEP) * MINUTE_STEP, page: 0,
            };
            const chosen = () => new Date(state.day.getFullYear(), state.day.getMonth(), state.day.getDate(), state.hour, state.minute);

            const dialog = make("dialog", "cn-sch");
            dialog.setAttribute("aria-labelledby", "cn-sch-title");
            dialog.appendChild(make("span", "cn-tag", "Schedule download"));
            const heading = make("h2", "", title);
            heading.id = "cn-sch-title";
            dialog.appendChild(heading);
            if (what) dialog.appendChild(make("p", "cn-sch-what", what));

            function section(label) {
                const box = make("div", "cn-sch-sec"), head = make("div", "cn-sch-sec-hd");
                head.appendChild(make("span", "cn-tag", label));
                box.appendChild(head);
                dialog.appendChild(box);
                return {box, head};
            }
            function cell(parent, label, onPick) {
                const button = make("button", "cn-sch-cell", label);
                button.addEventListener("click", () => { onPick(); render(); });
                parent.appendChild(button);
                return button;
            }

            const quick = section("Quick picks").box.appendChild(make("div", "cn-sch-quick"));
            const next = (hour, minute) => {
                const date = new Date();
                date.setHours(hour, minute, 0, 0);
                if (date <= Date.now()) date.setDate(date.getDate() + 1);
                return date;
            };
            [["In 1 hour", () => soon], ["Tonight 23:00", () => next(23, 0)],
             ["Night 02:00", () => next(2, 0)], ["Morning 06:00", () => next(6, 0)]].forEach(([label, target]) => {
                cell(quick, label, () => {
                    const date = target();
                    state.day = startOfDay(date); state.hour = date.getHours(); state.minute = date.getMinutes();
                    state.page = 0;
                });
            });

            const daySection = section("Day");
            const month = daySection.head.appendChild(make("span", "cn-sch-month"));
            const earlier = daySection.head.appendChild(make("button", "cn-sch-nav", "‹"));
            const later = daySection.head.appendChild(make("button", "cn-sch-nav", "›"));
            earlier.setAttribute("aria-label", "Earlier days");
            later.setAttribute("aria-label", "Later days");
            earlier.addEventListener("click", () => { state.page--; render(); });
            later.addEventListener("click", () => { state.page++; render(); });
            const days = daySection.box.appendChild(make("div", "cn-sch-grid cn-sch-days"));

            const hours = section("Hour").box.appendChild(make("div", "cn-sch-grid cn-sch-hours"));
            const hourCells = Array.from({length: 24}, (_, hour) => cell(hours, pad(hour), () => { state.hour = hour; }));
            const minutes = section("Minutes").box.appendChild(make("div", "cn-sch-grid cn-sch-mins"));
            const minuteCells = Array.from({length: 60 / MINUTE_STEP}, (_, i) =>
                cell(minutes, ":" + pad(i * MINUTE_STEP), () => { state.minute = i * MINUTE_STEP; }));

            const when = dialog.appendChild(make("div", "cn-sch-when"));
            when.setAttribute("role", "status");
            const whenDate = when.appendChild(make("b")), whenIn = when.appendChild(make("span"));

            const foot = dialog.appendChild(make("div", "cn-sch-foot"));
            const cancel = foot.appendChild(make("button", "cn-cta is-ghost", "Cancel"));
            const confirm = foot.appendChild(make("button", "cn-cta", confirmLabel));

            function render() {
                const now = new Date(), today = startOfDay(now);
                const first = addDays(today, state.page * DAYS_PER_PAGE);
                const last = addDays(first, DAYS_PER_PAGE - 1);
                month.textContent = first.getMonth() === last.getMonth()
                    ? format(first, {month: "long", year: "numeric"})
                    : format(first, {month: "short"}) + " – " + format(last, {month: "short", year: "numeric"});
                earlier.disabled = state.page === 0;
                later.disabled = state.page >= PAGES - 1;

                days.replaceChildren();
                for (let i = 0; i < DAYS_PER_PAGE; i++) {
                    const date = addDays(first, i);
                    const button = make("button", "cn-sch-cell");
                    button.appendChild(make("small", "", +date === +today ? "Today" : format(date, {weekday: "short"})));
                    button.appendChild(make("b", "", date.getDate()));
                    button.setAttribute("aria-label", format(date, {weekday: "long", day: "numeric", month: "long"}));
                    button.setAttribute("aria-pressed", String(+date === +state.day));
                    button.addEventListener("click", () => { state.day = date; render(); });
                    days.appendChild(button);
                }

                const isToday = +state.day === +today;
                hourCells.forEach((button, hour) => {
                    button.disabled = isToday && hour < now.getHours();
                    button.setAttribute("aria-pressed", String(hour === state.hour));
                });
                minuteCells.forEach((button, i) => {
                    const minute = i * MINUTE_STEP;
                    button.disabled = isToday && state.hour === now.getHours() && minute <= now.getMinutes();
                    button.setAttribute("aria-pressed", String(minute === state.minute));
                });

                const date = chosen(), past = date <= now;
                whenDate.textContent = whenLabel(date);
                whenIn.textContent = past ? "This time has already passed" : distance(date);
                when.classList.toggle("is-bad", past);
                confirm.disabled = past;
            }

            let result = null;
            cancel.addEventListener("click", () => dialog.close());
            confirm.addEventListener("click", () => { result = chosen(); dialog.close(); });
            dialog.addEventListener("click", event => { if (event.target === dialog) dialog.close(); });
            dialog.addEventListener("close", () => { dialog.remove(); resolve(result && result > Date.now() ? result : null); });

            render();
            document.body.appendChild(dialog);
            dialog.showModal();
        });
    }

    // ── "More" button on every download form ────────────────────────────────
    function isDownloadForm(form) {
        if (!form.querySelector('[name="item_payload"]')) return false;
        return !!form.querySelector('[name="download_type"]') ||
            new URL(form.action, location.href).pathname === config.downloadUrl;
    }

    function collect(form) {
        window.CinemaAudio?.prepareForm(form);
        const data = new FormData(form);
        data.delete("csrfmiddlewaretoken");
        // Same picker the download buttons read (see quality.js).
        const quality = form.closest(".cn-tile")?.querySelector(".cn-quality-picker select") ||
            document.querySelector("#cn-series-quality .cn-quality-picker select");
        if (quality) data.set("quality", quality.value);
        if (data.get("download_type") === "selected_seasons") {
            data.set("selected_seasons", [...document.querySelectorAll(".cn-season-pick:checked")].map(c => c.value).join(","));
        }
        return data;
    }

    function describe(data) {
        const kind = data.get("download_type"), season = data.get("season_number");
        if (kind === "full_series") return "Whole series";
        if (kind === "selected_seasons") return "Seasons " + data.get("selected_seasons").split(",").join(", ");
        if (kind === "full_season") return "Season " + season;
        if (kind) return "Season " + season + " · episodes " + data.get("selected_episodes");
        return "";
    }

    async function schedule(form) {
        const data = collect(form);
        const kind = data.get("download_type");
        if (kind === "selected_seasons" && !data.get("selected_seasons")) return toast("Select at least one season first.", {bad: true});
        if (kind === "episodes" && !data.get("selected_episodes")) return toast("Select at least one episode first.", {bad: true});
        let name = "";
        try { name = JSON.parse(data.get("item_payload")).name; } catch (_) {}

        const when = await pickTime(name || "Download", describe(data), "Schedule");
        if (!when) return;
        data.set("run_at", String(Math.floor(when.getTime() / 1000)));
        try {
            await post(config.addUrl, data);
            toast("Scheduled for " + whenLabel(when), {link: config.queueUrl});
            refresh();
        } catch (error) { toast(error.message, {bad: true}); }
    }

    let menu = null;
    function closeMenu() {
        if (!menu) return;
        menu.node.remove();
        menu.owner.setAttribute("aria-expanded", "false");
        menu = null;
    }
    function openMenu(owner, form) {
        const node = make("div", "cn-sch-menu");
        node.setAttribute("role", "menu");
        const item = make("button");
        item.setAttribute("role", "menuitem");
        item.innerHTML = CLOCK;
        item.appendChild(make("span", "", "Schedule…"));
        item.addEventListener("click", () => { closeMenu(); schedule(form); });
        node.appendChild(item);
        document.body.appendChild(node);

        const anchor = owner.getBoundingClientRect(), size = node.getBoundingClientRect();
        const below = anchor.bottom + 6 + size.height <= innerHeight;
        node.style.top = (below ? anchor.bottom + 6 : anchor.top - 6 - size.height) + "px";
        node.style.left = Math.max(8, Math.min(anchor.left, innerWidth - size.width - 8)) + "px";
        owner.setAttribute("aria-expanded", "true");
        menu = {node, owner};
        item.focus({preventScroll: true});
    }
    document.addEventListener("click", event => { if (menu && !menu.node.contains(event.target)) closeMenu(); });
    document.addEventListener("keydown", event => {
        if (event.key === "Escape" && menu) { const owner = menu.owner; closeMenu(); owner.focus(); }
    });
    addEventListener("scroll", closeMenu, {passive: true, capture: true});
    addEventListener("resize", closeMenu);

    document.querySelectorAll("form").forEach(form => {
        if (!isDownloadForm(form)) return;
        // The season batch form has no button of its own: it is fired from the tray.
        const submit = form.querySelector('button[type="submit"]') ||
            (form.id && document.querySelector('button[form="' + form.id + '"]')) ||
            (form.id === "cn-seasons-form" && document.getElementById("cn-seasons-btn"));
        if (!submit) return;
        const mini = submit.classList.contains("cn-mini");
        const dots = make("button", (mini ? "cn-mini" : "cn-cta is-ghost") + " cn-sch-dots", "•••");
        dots.title = "More options";
        dots.setAttribute("aria-label", "More options");
        dots.setAttribute("aria-haspopup", "menu");
        dots.setAttribute("aria-expanded", "false");
        dots.style.marginTop = submit.style.marginTop;
        dots.style.fontSize = submit.style.fontSize;
        if (submit.style.padding) dots.style.paddingBlock = submit.style.padding.split(" ")[0];
        dots.addEventListener("click", event => {
            event.stopPropagation();
            const reopen = !menu || menu.owner !== dots;
            closeMenu();
            if (reopen) openMenu(dots, form);
        });
        submit.insertAdjacentElement("afterend", dots);
    });

    // ── "Scheduled" strip on the Download page ──────────────────────────────
    const queue = document.getElementById("cn-reel-queue")?.closest("section");
    let strip = null, lines = null, count = null, signature = "";

    async function update(job, action, when) {
        const data = new FormData();
        data.set("id", job.id);
        data.set("action", action);
        if (when) data.set("run_at", String(Math.floor(when.getTime() / 1000)));
        try {
            await post(config.updateUrl, data);
            if (action === "move") toast("Moved to " + whenLabel(when));
        } catch (error) { toast(error.message, {bad: true}); }
        refresh();
    }

    function line(job) {
        const when = new Date(job.run_at * 1000);
        const row = make("div", "cn-ln cn-sch-line");
        const thumb = row.appendChild(make("span", "cn-thumb"));
        window.CN?.paint(thumb, job.title);
        if (job.poster) {
            const image = new Image();
            image.alt = ""; image.loading = "lazy"; image.decoding = "async";
            image.onerror = () => image.remove();
            image.src = job.poster;
            thumb.appendChild(image);
        }
        thumb.appendChild(make("b", "", job.title.slice(0, 1).toUpperCase()));

        const text = row.appendChild(make("span", "cn-grow"));
        text.appendChild(make("span", "cn-t", job.title));
        text.appendChild(make("span", "cn-s", [job.site, job.summary].filter(Boolean).join(" · ")));
        if (job.note) text.appendChild(make("span", "cn-s cn-sch-note", job.note));

        const right = row.appendChild(make("span", "cn-r"));
        if (job.status === "missed") right.appendChild(make("span", "cn-st is-warn", "Missed"));
        if (job.status === "failed") right.appendChild(make("span", "cn-st is-dead", "Failed"));
        right.appendChild(make("b", "", whenLabel(when)));
        if (job.status === "pending") right.appendChild(make("small", "", distance(when)));

        const actions = row.appendChild(make("span", "cn-sch-acts"));
        const button = (label, onClick) => actions.appendChild(make("button", "cn-cta is-ghost", label)).addEventListener("click", onClick);
        button("Change time", async () => {
            const moved = await pickTime(job.title, job.summary, "Save", when);
            if (moved) update(job, "move", moved);
        });
        button("Start now", () => update(job, "run"));
        button("Remove", () => update(job, "remove"));
        return row;
    }

    async function refresh() {
        if (!queue) return;
        let items;
        try {
            const response = await fetch(config.listUrl);
            if (!response.ok) return;
            items = (await response.json()).items || [];
        } catch (_) { return; }

        if (!strip) {
            strip = make("section", "cn-strip");
            const head = strip.appendChild(make("div", "cn-strip-hd"));
            head.appendChild(make("h2", "", "Scheduled"));
            count = head.appendChild(make("span", "cn-n"));
            lines = strip.appendChild(make("div", "cn-lines"));
            queue.insertAdjacentElement("beforebegin", strip);
        }
        // Rebuild only on a real change (or a new minute, for the countdown): keeps focus on the buttons.
        const fresh = JSON.stringify(items) + Math.floor(Date.now() / 60000);
        if (fresh !== signature) {
            signature = fresh;
            lines.replaceChildren(...items.map(line));
            count.textContent = items.length;
        }
        strip.hidden = items.length === 0;
    }

    if (queue) {
        refresh();
        setInterval(() => { if (!document.hidden && !document.querySelector("dialog.cn-sch")) refresh(); }, REFRESH_MS);
    }
})();
