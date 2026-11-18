(function () {
    "use strict";
    const node = document.getElementById("cn-i18n");
    window.CN_LANG = document.documentElement.lang || "en";
    window.t = function (text) { return text; };
    if (!node) return;

    let catalog = {};
    try { catalog = JSON.parse(node.textContent || "{}"); } catch (_) { return; }

    const exact = Object.create(null);
    const patterns = [];
    const normalize = text => String(text).replace(/\s+/g, " ").trim();
    const escapeRe = text => text.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");

    Object.keys(catalog).forEach(key => {
        const source = normalize(key);
        if (!/\{\w+\}/.test(source)) { exact[source] = catalog[key]; return; }
        const names = [];
        const body = escapeRe(source).replace(/\\\{(\w+)\\\}/g, (_, name) => { names.push(name); return "(.+?)"; });
        patterns.push({re: new RegExp("^" + body + "$"), names, out: catalog[key]});
    });

    function translate(text) {
        if (typeof text !== "string" || !text) return text;
        const core = normalize(text);
        if (!core) return text;
        let out = exact[core];
        if (out === undefined) {
            for (const pattern of patterns) {
                const match = pattern.re.exec(core);
                if (!match) continue;
                out = pattern.out.replace(/\{(\w+)\}/g, (whole, name) => {
                    const index = pattern.names.indexOf(name);
                    return index < 0 ? whole : translate(match[index + 1]);
                });
                break;
            }
        }
        if (out === undefined) out = translateSegments(core);
        if (out === undefined || out === core) return text;
        return text.match(/^\s*/)[0] + out + text.match(/\s*$/)[0];
    }
    // "Series · 3 seasons" or "2 hours, 3 minutes": translate each piece when the whole has no entry.
    function translateSegments(core) {
        for (const separator of [" · ", ", "]) {
            if (!core.includes(separator)) continue;
            let changed = false;
            const pieces = core.split(separator).map(piece => {
                const next = translate(piece);
                if (next !== piece) changed = true;
                return next;
            });
            if (changed) return pieces.join(separator);
        }
        return undefined;
    }
    window.t = translate;

    const SKIP_TAGS = new Set(["SCRIPT", "STYLE", "CODE", "PRE", "TEXTAREA", "NOSCRIPT"]);
    // Content that comes from providers (titles, captions, log lines) must never be translated.
    const SKIP_SELECTOR = "[data-no-tr], .cn-ptitle, .cn-cap, .cn-t";
    const ATTRIBUTES = ["placeholder", "title", "aria-label", "alt"];

    function skipped(element) {
        for (let current = element; current && current.nodeType === 1; current = current.parentElement) {
            if (SKIP_TAGS.has(current.tagName) || current.matches(SKIP_SELECTOR)) return true;
        }
        return false;
    }
    function textNode(target) {
        const next = translate(target.nodeValue);
        if (next !== target.nodeValue && !skipped(target.parentElement)) target.nodeValue = next;
    }
    function attributes(element) {
        if (skipped(element)) return;
        ATTRIBUTES.forEach(name => {
            const value = element.getAttribute(name);
            if (value) {
                const next = translate(value);
                if (next !== value) element.setAttribute(name, next);
            }
        });
        if (element.tagName === "INPUT" && /^(submit|button)$/.test(element.type) && element.value) {
            element.value = translate(element.value);
        }
    }
    function walk(root) {
        if (root.nodeType === 3) { textNode(root); return; }
        if (root.nodeType !== 1 && root.nodeType !== 9) return;
        if (root.nodeType === 1) attributes(root);
        const scope = root.nodeType === 9 ? root.documentElement : root;
        const walker = document.createTreeWalker(scope, NodeFilter.SHOW_TEXT);
        for (let current = walker.nextNode(); current; current = walker.nextNode()) textNode(current);
        scope.querySelectorAll("[placeholder],[title],[aria-label],[alt],input[type=submit],input[type=button]").forEach(attributes);
    }

    ["alert", "confirm", "prompt"].forEach(name => {
        const original = window[name];
        window[name] = function (message, ...rest) { return original.call(window, translate(String(message)), ...rest); };
    });

    new MutationObserver(records => {
        records.forEach(record => {
            if (record.type === "characterData") textNode(record.target);
            else if (record.type === "attributes") attributes(record.target);
            else record.addedNodes.forEach(walk);
        });
    }).observe(document.documentElement, {
        subtree: true, childList: true, characterData: true, attributes: true, attributeFilter: ATTRIBUTES,
    });

    if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", () => walk(document));
    else walk(document);
})();
