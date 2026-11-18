# Sequent Remote — Mobile Web Dashboard

A responsive phone webpage that uses **ntfy as the command and notification backend**. Cloudflare Workers serves the webpage, authenticates your session, signs Sequent commands, and sends them over HTTPS to ntfy. **Sequent itself never opens an inbound port** and continues to use only Python's standard library and native Windows APIs. No Discord setup is needed.

**The site is not deployed for you.** You must deploy `web_dashboard/worker.mjs` using a Cloudflare account, set its private secrets, and activate Web Dashboard in Sequent. The downloaded `panel.html` is a design/preview copy; it does not work standalone without the Worker API. **The Worker now also serves the live panel at `/panel.html`**.

## Architecture

```
Your phone's browser  → HTTPS → Cloudflare Worker (login + signed commands)
                                       ↓ HTTPS publish
                              ntfy command topic
                                       ↓ HTTPS polling (outbound)
                                  Sequent on PC
                                       ↓
                          ntfy phone notification topic
                                       ↓
                    Your ntfy app + recent web activity
```

**Command delivery to ntfy is NOT proof that a macro ran.** Sequent must be on and connected, and still enforces the local permissions, approved presets, and queue-clean/idle requirements.

## 1. Update Sequent on Windows

1. Exit Sequent and back up `Sequent.py`, `sequent_remote.py`, and the private `%LOCALAPPDATA%\Sequent\remote.json` config.
2. Replace `Sequent.py` and `sequent_remote.py` with the versions in this ZIP. Keep both files together. Do not change the location of your existing private `remote.json`.
3. Open Settings → **Open Sequent Remote Console** (`Ctrl+Shift+R`).
4. Check **Enable remote receiver** and **Allow signed commands from the Web Dashboard**. Leave Discord Bridge off unless you're using it.
5. For macro launching, separately enable **Allow launching approved presets** and select individual saved presets. For screenshots, separately enable **Allow screenshot requests from phone**.
6. Set the same ntfy server and phone topic you've been using. Click **Save & Connect**. The receiver should become connected. Web Dashboard stays off until explicitly enabled in this step.
7. In the Remote Console's existing Discord Bridge card, use **Copy ntfy server**, **Copy command topic**, and **Copy signing secret** to get the values for the Worker. Despite those button labels, the same command topic/key are used by the web dashboard. Treat the signing secret as a password. Clear your clipboard after configuring the Worker.

## 2. Deploy one Cloudflare Worker

1. Open https://dash.cloudflare.com/ → **Workers & Pages** → create a Worker.
2. Replace the starter code with **the entire content of `web_dashboard/worker.mjs`** and deploy. This is one JavaScript file, with its HTML/CSS/JS already included and **no npm dependencies**.
3. In Worker Settings, configure these **plain text variables**:

| Variable | Example / meaning |
| --- | --- |
| `NTFY_SERVER` | `https://ntfy.sh` (or your approved HTTPS ntfy server) |
| `NTFY_COMMAND_TOPIC` | Exact inbox command topic copied from Sequent |
| `NTFY_PHONE_TOPIC` | Exact phone notification topic from Remote Console (optional; enables recent activity) |
| `APPROVED_PRESETS_JSON` | JSON array of names, e.g. `["Demo Macro", "Nightly Report"]`. Must also be locally approved in Sequent. Leave as `[]` for controls only. |

4. Add these as **Worker secrets** (not regular variables):

| Secret | Value |
| --- | --- |
| `SEQUENT_REMOTE_SECRET` | The full 64-character hex signing secret copied from Sequent Remote Console |
| `DASHBOARD_PASSWORD` | A unique, strong private passphrase of **at least 16 characters**, used to log in on your phone |
| `SESSION_SECRET` | A different random secret of **at least 32 characters** for browser-session signatures |
| `NTFY_TOKEN` | Only if your **self-hosted/authenticated** ntfy server requires a bearer access token; otherwise omit |

To generate a good `SESSION_SECRET`, run on your PC:

```powershell
python -c "import secrets; print(secrets.token_urlsafe(48))"
```

Choose your own unique dashboard passphrase; don't reuse the Sequent signing secret as the login password. Secrets stay on the Worker and are never embedded in the browser page.

5. Open your HTTPS Worker URL on your phone (for example `https://your-sequent-panel.yourname.workers.dev/panel.html`). Sign in using your private dashboard password. You can use Chrome's **Add to Home screen** option to create an app-like shortcut.
6. Try **Request status** first; then click **Refresh** under Recent Notifications. If status reaches your ntfy phone topic, verify that it also appears there. Next test Pause/Resume on a harmless macro. Only after those tests should you enable macro launches or screenshots.

## Web Dashboard controls

- Pause, Resume, Stop (Stop asks for confirmation)
- Request Sequent status or desktop screenshot (screenshot scope is chosen on the PC)
- Run a **locally AND dashboard-allowlisted** macro
- Schedule a locally approved macro in 5, 15, or 30 minutes
- View Sequent's recent phone-topic notifications and screenshot attachments, where accessible; refresh automatically every 15 seconds while the page is open
- Login with a strong passphrase; signed HTTP-only, Secure, SameSite=Strict session cookie; **six-hour expiry**; sign out clears the local cookie

The screenshot feature returns to your existing ntfy notification topic. The web panel displays that attachment if the ntfy server provides a valid attachment URL in recent messages; it does not stream your desktop continuously.

## Security and privacy

- Do **not** put `SEQUENT_REMOTE_SECRET`, `DASHBOARD_PASSWORD`, `SESSION_SECRET`, or `NTFY_TOKEN` into `panel.html`, GitHub, or frontend JavaScript. Only set them as Worker secrets.
- The public `ntfy.sh` service uses **public topics**: a hard-to-guess topic is not a substitute for authenticated access. The command signing prevents unauthorized macro execution, but it does **not** make messages or screenshots on a public topic confidential. For work data, use an organization-approved private ntfy server, or do not transmit it.
- Protect the Cloudflare Worker with **Cloudflare Access (MFA)** and/or WAF rate limiting where possible. The included in-memory login-attempt throttle is not a distributed, durable brute-force defense.
- The panel uses HTTPS, HMAC-SHA256 authenticated command envelopes, 120-second command expiry, random nonces, separate Web Dashboard permission, and Sequent's built-in local allowlists. It has no arbitrary shell or Python execution.
- Log out on shared devices. Stateless cookies are valid until their six-hour expiry unless you rotate `SESSION_SECRET`; logging out only clears that browser's cookie.
- Disabling Web Dashboard in Sequent immediately prevents new web-signed commands from being accepted on subsequent polling. You can also rotate the signing secret in the Remote Console if you suspect exposure (which requires updating the Worker).
- Do not enable remote control on employer-managed PCs without explicit authorization. GUI macros usually need a logged-in, interactive Windows session; some operations won't work with the computer locked.

## Troubleshooting

| Symptom | What to check |
| --- | --- |
| Dashboard says configuration incomplete | All Worker secrets/variables set exactly; check Worker logs |
| Invalid passphrase | `DASHBOARD_PASSWORD` is set as a Worker secret; mind case and spaces |
| Command delivered, nothing happens | Sequent is running; Remote receiver is connected; Web Dashboard permission is on; same server/topic/signing secret |
| Macro refused | Both allowlists contain the exact saved preset name; Sequent isn't busy; queue has no unsaved changes |
| Screenshot fails | Enable screenshot permission in Remote Console; check ntfy attachment size/network restrictions |
| Activity unavailable | `NTFY_PHONE_TOPIC` matches Sequent phone topic, and `NTFY_TOKEN` has read permissions if needed; network access to ntfy |
| Page is accessible without login | Static HTML may load without login, but all command APIs require a signed-in session. The Worker keeps all private keys. Use Cloudflare Access to protect the entire URL if you wish. |

## Validation performed

- Python syntax compilation of `Sequent.py` and `sequent_remote.py`
- Worker JavaScript parsing, HTML client JavaScript parsing
- Simulated Worker login, session, origin protection, allowlists, ntfy publishing and activity retrieval
- Cross-language Worker → Python signed v3 command verification, replay/expiry and separate permission checks
- Existing v1 ntfy phone buttons still accepted
- Live Tkinter Remote Console checks in a virtual display: Web Dashboard opt-in saves and fixed Save & Connect stays visible

**Not yet verified:** actual Cloudflare deployment, end-to-end ntfy delivery over the internet, or real Windows phone-to-macro execution. Set up those services before calling this production-ready.


## Request the live dashboard through ntfy (new)

ntfy carries the **request and the clickable response**, while the Cloudflare Worker hosts the live webpage. No separate `panel.html` file needs to be downloaded to the phone.

1. **Deploy and test the Worker first.** Verify its root URL or `/panel.html` shows the dashboard login screen. The page must remain password-protected even if the link is shared.
2. In **Sequent → Settings → Open Sequent Remote Console**, enable **Enable remote receiver** and **Allow signed commands from the Web Dashboard**. In the **Hosted dashboard URL (HTTPS)** field, paste the deployed Worker URL such as `https://your-panel.your-name.workers.dev/panel.html`. Click **Save & Connect**.
3. To send your first link, click **Send Dashboard Link** under **Quick Actions**. Your phone's ntfy topic receives a **Sequent Remote • Dashboard** notification; tap the notification or **Open Dashboard** action to open the login-protected panel.
4. To request that link later **from ntfy itself**, click **Send Phone Controls** once in the Remote Console. The **Sequent Remote • Inspect** notification now has **Status**, **Screenshot**, and **Get Dashboard** actions. On your phone, tap **Get Dashboard**. That posts a **signed, single-use** `dashboard` request to Sequent's command topic. Sequent replies to the phone topic with the clickable dashboard link. The Windows application must be running and its receiver connected.
5. Sign in with `DASHBOARD_PASSWORD` on the Worker. The notification link does **not** contain a password, token, one-time login bypass, or private signing key.

**Important:** Posting plain text like `/sequent dashboard` to an ntfy topic is deliberately **not accepted** as a remote command, because on public topics anyone may publish messages. Use the signed **Get Dashboard** phone action instead. The link only opens a login page; the full dashboard requires the separate Worker deployment and authentication.

The hosted URL is validated and restricted to HTTPS, with only the root path `/` or `/panel.html`; query strings, fragments and embedded URL credentials are rejected. If you change the URL, click **Save & Connect** again. Disable Web Dashboard if you do not want signed dashboard-link requests to work.

ntfy notification click/view URLs: https://docs.ntfy.sh/publish/#click-action
Cloudflare Worker secrets: https://developers.cloudflare.com/workers/configuration/secrets/
