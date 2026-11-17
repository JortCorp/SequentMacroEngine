# Sequent — Send Phone Alert / Send Email

This build extends the **Request Form → JSON** version of Sequent and preserves its Action Queue behavior. No third-party Python dependencies.

## Phone push using ntfy (easiest)
1. Install the **ntfy** app on your Android phone.
2. Subscribe to a long, hard-to-guess topic you create (letters/numbers/hyphens/underscores, 5–64 characters).
3. In the Action Library under **Variables & Inputs**, add **Send Phone Alert**.
4. Service: `ntfy`; Server: `https://ntfy.sh`; Topic: exactly the topic you subscribed to.
5. Set Title and Message. Use `${your_variable}` to include values available to Sequent.
6. Run the action. Phone notifications require Internet access and a working ntfy subscription.

**Security:** On public ntfy.sh, anyone who discovers your topic can subscribe or publish. A hard-to-guess topic is not strong access control. Do NOT send confidential, employee, customer, aircraft or other company data to a public third-party notification service without authorization. For sensitive alerts, use an approved private/authenticated notification service.

## Phone push using Pushover
1. Install Pushover, register an application, and obtain the app token and your user key at https://pushover.net .
2. Set Windows *user* environment variables named `SEQUENT_PUSHOVER_TOKEN` and `SEQUENT_PUSHOVER_USER`, each holding the appropriate value. Restart Sequent after setting environment variables.
3. Add **Send Phone Alert**, choose `Pushover`, and leave the two environment-variable *names* in their fields.

Do not paste the actual Pushover token or key into the action fields; exportable macros should contain only environment-variable names. User environment variables are not encrypted storage: other programs running under your account may read them.

## Email via SMTP
1. Add **Send Email** under **Variables & Inputs**.
2. Enter From, To, Subject, Body, SMTP host, port, and security.
3. For personal Gmail SMTP, host `smtp.gmail.com`, port `587` and `STARTTLS` are common. Use an **app password** if your account supports it; do not use your regular account password. Account restrictions may prevent this. With an organization-managed account, use only an approved mail service and authentication method.
4. If SMTP authentication is required, enter your SMTP username and specify the environment-variable name `SEQUENT_SMTP_PASSWORD`. Set that Windows user environment variable to your app password; restart Sequent.
5. Run the macro. Only one recipient is supported per action.

**Setting a Windows user environment variable:** Windows Search → "Edit environment variables for your account" → New under User variables. Enter the Name and Value. Close and restart Sequent after setting it. Values stored here are not encrypted. Do not commit credentials to git or embed them in a macro.

## Error handling and limitations
- Actions send on playback, not in a background service. Your computer must be running, connected, and permitted to reach the endpoint.
- Delivery is accepted by the remote service; this does not guarantee that a device displayed the push alert or that an email arrived in the inbox.
- Network requests time out after 15 seconds. Existing Sequent action recovery policy applies if sending fails.
- This implementation has been syntax-checked, imported into a Tkinter test host, and tested with mocked HTTPS and SMTP responses. Real delivery and the full Windows UI have **not** been verified.
