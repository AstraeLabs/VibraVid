## CookieInspector

Most services below need data from your logged-in browser session. Open the service's site, log in, click the [CookieInspector] extension (from DISCORD), then use:

| Button | Reads | Used for |
|--------|-------|----------|
| **GET COOKIES** | Cookies of the current site | Crunchyroll, Discovery+, HBO Max, Prime Video, Apple TV |
| **GET STORAGE** | Everything in `localStorage` | Mediaset Infinity (`rtilogin_acd`), or anything to look up by hand |
| **GET TOKEN** | Only `access_token` / `refresh_token` from `localStorage` | Disney+ |

- The **filter** box keeps only keys (or values) containing your text (e.g. `st`); **COPY JSON** copies exactly what is shown, on a single line.
- In `login.toml` wrap copied JSON in triple **single** quotes (`cookies = '''{"key":"value"}'''`): the text stays exactly as copied (backslashes and `\n` are not interpreted), while plain TOML strings can't hold raw quotes.

Every service section also has `args = ""`: options added to each download of that service (e.g. `args = "--quality 1080"`). See [Default options for a service](configuration.md#default-options-for-a-service-args).

### Account profile (`profile`)

For services with several profiles per account, `profile` picks the one to download with, instead of the profile your cookies were exported from:

```toml
[netflix]
profile = "2"        # its number in the profile list ...
# profile = "Kids"   # ... or its name (case does not matter)
```

Empty (or missing) keeps the profile of your cookies. The run prints `Profile: <name>`; a number or name that does not exist stops the run and lists the profiles with their numbers (`1 Adults, 2 Kids`). For a single run use `--user-profile 2` (or the name), which wins over `login.toml`. Netflix's own `--profile` option is something else: it selects the codec profile group (HPL, MPL, P1...).

---

## Cookie-based services

Open the site, log in, click the extension, **GET COOKIES** -> **COPY JSON**, and paste it in `Conf/login.toml`:

| Service | Site | `login.toml` section |
|---------|------|----------------------|
| Crunchyroll | [crunchyroll.com](https://www.crunchyroll.com/) | `[crunchyroll]` |
| Discovery+ [EU] | [play.discoveryplus.com](https://play.discoveryplus.com/) | `[discoveryplus]` |
| HBO Max | [play.hbomax.com](https://play.hbomax.com/) | `[hbomax]` |
| Amazon Prime Video [EU] | [primevideo.com](https://www.primevideo.com/) | `[primevideo]` |
| Apple TV+ | [tv.apple.com](https://tv.apple.com/) | `[appletv]` |

```toml
[crunchyroll]
cookies = '''<paste_copied_json_here>'''
```

**Discovery+ / HBO Max without the extension**: Developer Tools (<kbd>F12</kbd>) -> **Application** -> **Cookies**, find the `st` cookie and set `st = "<value>"` in the `[discoveryplus]` / `[hbomax]` section.

![st location](assets/login/discoveryplus_eu_st.png)

---

## Mediaset Infinity

1. Open [Mediaset](https://mediasetinfinity.mediaset.it/) and log in (pick your profile).
2. Extension -> **GET STORAGE**, type `rtilogin_acd` in the **filter** box, **Copy JSON**.
3. Add it to `Conf/login.toml`:

```toml
[mediasetinfinity]
tv_login = false
rtilogin_acd = '''<paste_copied_value_here>'''
```

The template ships `rtilogin_acd = '''[paste here]'''`: replace `[paste here]` with the copied value, or leave it to log in anonymously.

Without the extension: <kbd>F12</kbd> -> **Application** -> **Local Storage** -> `mediasetinfinity.mediaset.it`, copy the `rtilogin_acd` value.

---

## Disney+

1. Open [Disney+](https://www.disneyplus.com/) and log in.
2. Extension -> **GET TOKEN** (shows only `access_token` and `refresh_token`) -> **Copy JSON**.
3. Add it to `Conf/login.toml`:

```toml
[disney]
token = "<access_token>"
refresh_token = "<refresh_token>"
```

`access_token` or `token` both work for the session token; `refresh_token` renews it, and renewed values are saved back to the `disney` section after the first run.

---

## Tubi TV: plain credentials

Email/password, no extension. Without them, search returns no results for this site.

```toml
[tubi]
email = "your@email.com"
password = "your-password"
```
