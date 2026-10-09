## CookieInspector: what the buttons do

Most services below need data copied from your logged-in browser session. The
[CookieInspector] extension (from DISCORD) does it in a few clicks. Open the service's site,
log in, click the extension icon, then use:

| Button | What it reads | Used for |
|--------|---------------|----------|
| **GET COOKIES** | The cookies of the current site | Crunchyroll, Discovery+, HBO Max, Prime Video, Apple TV |
| **GET STORAGE** | Everything in the page's `localStorage` | Anything else you need to look up by hand |
| **GET TOKEN** | Only `access_token` / `refresh_token`, found inside the page's `localStorage` | Disney+ |

- The **filter** box keeps only the keys (or values) containing your text, e.g. `st`, and **COPY JSON**
  copies exactly what is shown.
- **COPY JSON** puts the JSON on a single line, so it can be pasted straight into `Conf/login.json`
  without breaking it.

---

## Crunchyroll: Get Cookies via Extension

### Prerequisites

- Install the [CookieInspector] browser extension from DISCORD

### Steps

1. **Open** [Crunchyroll](https://www.crunchyroll.com/) and **log in** with your credentials.
2. **Click** the CookieInspector extension icon in your browser toolbar.
3. **Click** "Get Cookies" button.
4. **Click** "Copy JSON" to copy the authentication data.
5. Add it to `Conf/login.json`:
   ```json
   "crunchyroll": <paste_copied_json_here>
   ```

---

## Mediaset Infinity

### Steps

1. **Open** [Mediaset](https://mediasetinfinity.mediaset.it/) and **log in** (pick your profile).
2. **Click** the CookieInspector extension icon, then **GET STORAGE**.
3. Type `rtilogin_acd` in the **filter** box and **click** "Copy JSON".
4. Add it to `Conf/login.json`:
   ```json
   "mediasetinfinity": <paste_copied_json_here>
   ```
   The result looks like `"mediasetinfinity": {"rtilogin_acd": {"caToken": "...", "persona": {"id": "..."}, ...}}`.

Without the extension: open Developer Tools (<kbd>F12</kbd>) → **Application** tab → **Local Storage** →
`mediasetinfinity.mediaset.it`, copy the value of the `rtilogin_acd` key and put it in
`"mediasetinfinity": {"rtilogin_acd": <value>}`.

---

## Discovery+ [EU]

### Steps

1. **Open** [Discovery+](https://play.discoveryplus.com/) and **log in**.
2. **Click** the CookieInspector extension icon, then **GET COOKIES**.
3. **Click** "Copy JSON" and add it to `Conf/login.json` (only the `st` cookie is used):
   ```json
   "discoveryplus": <paste_copied_json_here>
   ```

Without the extension: open Developer Tools (<kbd>F12</kbd>) → **Application** tab → **Cookies**, search for
the `st` cookie and copy its value into `"discoveryplus": {"st": "<value>"}`.

### Screenshot Reference
![st location](assets/login/discoveryplus_eu_st.png)

---

## HBO Max

### Steps

1. **Open** [HBO Max](https://play.hbomax.com/) and **log in**.
2. **Click** the CookieInspector extension icon, then **GET COOKIES**.
3. **Click** "Copy JSON" and add it to `Conf/login.json` (only the `st` and `session` cookies are used):
   ```json
   "hbomax": <paste_copied_json_here>
   ```

Without the extension, copy the `st` cookie value by hand as shown for Discovery+ and write it to
`"hbomax": {"st": "<value>"}`.

---

## Disney+

### Steps

1. **Open** [Disney+](https://www.disneyplus.com/) and **log in**.
2. **Click** the CookieInspector extension icon, then **GET TOKEN**. It reads the session out of the
   page's storage and shows only `access_token` and `refresh_token`.
3. **Click** "Copy JSON" and add it to `Conf/login.json`:
   ```json
   "disney": <paste_copied_json_here>
   ```

Disney+ accepts `access_token` or `token` for the session token, and uses `refresh_token` to renew it. After the
first run the renewed values are saved back to the same `disney` section.

---

## Amazon Prime Video [EU]: Get Cookies via Extension

### Prerequisites

- Install the [CookieInspector] browser extension from DISCORD

### Steps

1. **Open** [Prime Video](https://www.primevideo.com/) and **log in**.
2. **Click** the CookieInspector extension icon in your browser toolbar.
3. **Click** "Get Cookies" button.
4. **Click** "Copy JSON" to copy the authentication data.
5. Add it to `Conf/login.json`:
   ```json
   "primevideo": <paste_copied_json_here>
   ```

---

## Tubi TV: Plain Credentials

Unlike the cookie-based services above, Tubi TV authenticates with a plain email/password pair
— no browser extension needed.

### Steps

1. Add your Tubi TV account credentials to `Conf/login.json`:
   ```json
   "tubi": {
     "email": "your@email.com",
     "password": "your-password"
   }
   ```

Without these set, search returns no results for this site.

---

## Apple TV+: Get Cookies via Extension

### Prerequisites

- Install the [CookieInspector] browser extension from DISCORD

### Steps

1. **Open** [Apple TV+](https://tv.apple.com/) and **log in**.
2. **Click** the CookieInspector extension icon in your browser toolbar.
3. **Click** "Get Cookies" button.
4. **Click** "Copy JSON" to copy the authentication data.
5. Add it to `Conf/login.json`:
   ```json
   "appletv": <paste_copied_json_here>
   ```