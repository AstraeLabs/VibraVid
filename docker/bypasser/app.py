# 17.07.26

import json
import logging
import os
import re
import time

from flask import Flask, jsonify, request

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("bypasser")

app = Flask(__name__)
CHALLENGE_MARKERS = ("just a moment", "attention required", "cf-browser-verification")
DEFAULT_TIMEOUT = 60


def solve_turnstile(url: str, sitekey: str, timeout: int = DEFAULT_TIMEOUT, action: str | None = None):
    from seleniumbase import SB

    render_options = {
        "sitekey": sitekey,
        "appearance": "interaction-only",
        "execution": "execute",
        "theme": "auto",
    }
    if action:
        render_options["action"] = action

    t0 = time.time()
    with SB(uc=True, headless=True, incognito=True, locale="en") as sb:
        sb.uc_open_with_reconnect(url, reconnect_time=4)
        sb.sleep(1)

        sb.execute_script(
            """
            if (!document.getElementById('__bypasser_container')) {
                const d = document.createElement('div');
                d.id = '__bypasser_container';
                d.style.display = 'none';
                document.body.appendChild(d);
            }
            window.__bypasser_token = null;
            window.__bypasser_error = null;
            if (!window.turnstile) {
                const s = document.createElement('script');
                s.src = 'https://challenges.cloudflare.com/turnstile/v0/api.js?render=explicit';
                document.head.appendChild(s);
            }
            """
        )

        deadline = time.time() + timeout
        while time.time() < deadline and not sb.execute_script("return !!window.turnstile"):
            sb.sleep(0.5)
        if not sb.execute_script("return !!(window.turnstile && window.turnstile.render)"):
            raise RuntimeError("Turnstile script did not load in time.")

        sb.execute_script(
            f"""
            const id = window.turnstile.render(document.getElementById('__bypasser_container'), Object.assign(
                {json.dumps(render_options)},
                {{
                    callback: (t) => {{ window.__bypasser_token = t; }},
                    'error-callback': () => {{ window.__bypasser_error = 'error'; }},
                    'expired-callback': () => {{ window.__bypasser_error = 'expired'; }},
                }}
            ));
            window.turnstile.execute(id);
            """
        )

        deadline = time.time() + timeout
        token = None
        while time.time() < deadline:
            token = sb.execute_script("return window.__bypasser_token;")
            if token:
                break
            err = sb.execute_script("return window.__bypasser_error;")
            if err:
                raise RuntimeError(f"Turnstile {err}.")
            sb.sleep(0.5)

        if not token:
            raise RuntimeError("Timed out waiting for the Turnstile token.")

        return token, round(time.time() - t0, 1)


@app.route("/solve", methods=["POST"])
def solve():
    data = request.get_json(force=True, silent=True) or {}
    url = data.get("url")
    sitekey = data.get("sitekey")
    action = data.get("action") or None
    timeout = int(data.get("timeout") or DEFAULT_TIMEOUT)

    if not url or not sitekey:
        return jsonify({"status": "error", "message": "Missing 'url' or 'sitekey'."}), 400

    logger.info(f"Solving Turnstile for url={url!r} sitekey={sitekey!r} action={action!r}")
    try:
        token, elapsed = solve_turnstile(url, sitekey, timeout=timeout, action=action)
        logger.info(f"Solved in {elapsed}s")
        return jsonify({"status": "ok", "token": token, "elapsed": elapsed})
    except Exception as e:
        logger.exception("Solve failed")
        return jsonify({"status": "error", "message": str(e)}), 500


def _is_challenge(sb) -> bool:
    title = (sb.cdp.get_title() or "").lower()
    return any(marker in title for marker in CHALLENGE_MARKERS)


def _extract(sb, html: str, rules: dict) -> dict:
    """Pull just the needed values out of the page."""
    out = {}
    for name, rule in (rules or {}).items():
        if not isinstance(rule, dict):
            continue
        if rule.get("css"):
            attr = rule.get("attr")
            elements = sb.cdp.select_all(rule["css"])
            values = [el.attrs.get(attr) if attr else el.text for el in elements]
            out[name] = [v.strip() if isinstance(v, str) else v for v in values if v]
        elif rule.get("regex"):
            matches = re.finditer(rule["regex"], html, re.IGNORECASE | re.DOTALL)
            out[name] = [m.group(1) if m.groups() else m.group(0) for m in matches]
    return out


def fetch_page(url: str, timeout: int = DEFAULT_TIMEOUT, wait_for: str | None = None, extract: dict | None = None, include_html: bool = True):
    from seleniumbase import SB

    t0 = time.time()
    # CDP mode drives Chrome without a webdriver, which is what lets the Cloudflare challenge pass;
    # the plain headless/webdriver path is refused. The window lives in Xvfb (see Dockerfile).
    with SB(uc=True, test=True, headless=False, xvfb=True, incognito=True, locale="en") as sb:
        sb.activate_cdp_mode(url)
        sb.sleep(4)

        deadline = time.time() + timeout
        while _is_challenge(sb) and time.time() < deadline:
            try:
                sb.cdp.solve_captcha()
            except Exception as e:
                logger.debug(f"captcha solve skipped: {e}")
            sb.sleep(5)

        if _is_challenge(sb):
            raise RuntimeError("Cloudflare challenge was not solved in time.")

        if wait_for:
            sb.cdp.wait_for_element_present(wait_for, timeout=max(1, int(deadline - time.time())))

        html = sb.cdp.get_page_source()
        result = {
            "url": sb.cdp.get_current_url(),
            "title": sb.cdp.get_title(),
            "user_agent": sb.cdp.get_user_agent(),
            "cookies": [{"name": c.name, "value": c.value, "domain": c.domain} for c in sb.cdp.get_all_cookies()],
            "extracted": _extract(sb, html, extract),
            "elapsed": round(time.time() - t0, 1),
        }
        if include_html:
            result["html"] = html
        return result


@app.route("/fetch", methods=["POST"])
def fetch():
    """Load a URL in a real browser, ride out the Cloudflare challenge and return what is needed."""
    data = request.get_json(force=True, silent=True) or {}
    url = data.get("url")
    if not url:
        return jsonify({"status": "error", "message": "Missing 'url'."}), 400

    timeout = int(data.get("timeout") or DEFAULT_TIMEOUT)
    logger.info(f"Fetching url={url!r}")
    try:
        result = fetch_page(
            url,
            timeout=timeout,
            wait_for=data.get("wait_for") or None,
            extract=data.get("extract"),
            include_html=data.get("html", True),
        )
        logger.info(f"Fetched in {result['elapsed']}s")
        return jsonify({"status": "ok", **result})
    except Exception as e:
        logger.exception("Fetch failed")
        return jsonify({"status": "error", "message": str(e)}), 500


@app.route("/health", methods=["GET"])
def health():
    return jsonify({"status": "ok"})


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 8192)))
