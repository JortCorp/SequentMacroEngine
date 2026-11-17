# Sequent — ntfy screenshot notifications

This update extends **Send Phone Alert** in the previous Sequent_Phone_Email_Notifications build. It does not modify the Action Queue interaction logic or prior JSON input actions.

## Setup

1. In the Action Library, open **Variables & Inputs → Send Phone Alert**.
2. Choose **ntfy** as the service. Configure the HTTPS server, topic, title, and message as usual.
3. Enable **Attach desktop screenshot to ntfy alert**.
4. Choose the **Screenshot area**:
   - **All Monitors** (default): includes the whole virtual Windows desktop, even monitors at negative coordinates.
   - **Monitor at Cursor**: captures only the display containing the mouse at the moment of the notification. Use this if you want a more readable image.
5. Set **Max screenshot width** (default: **3840 pixels**; permitted: 800–7680). Very wide desktops are downscaled proportionally to fit the width. Images are also limited to ~12 megapixels in memory.
6. Keep **Mark cursor position on screenshot** enabled to draw a high-contrast ring over the cursor's position. Windows screen captures often omit the native cursor, so this explicitly draws it.
7. Optionally leave **Include cursor coordinates in message** enabled; this adds a line such as `Mouse location at capture: (512, 330)` to the notification.
8. Save the action. On playback, Sequent captures the desktop *at the moment this action executes* and sends the image and message as **one** ntfy notification.

You may reference local or global variables in the notification title, message, and ntfy topic using Sequent's existing `${variable}` interpolation. The screenshot marker corresponds to the **live cursor location when the screenshot is taken**; it is not a history of the last clicked pixel if the cursor was moved afterwards.

## How it works

- Windows: Win32 GDI (`ctypes`) captures the desktop; Python standard-library `zlib`/`struct` encode a PNG. There are **no third-party Python packages** and no screenshot file needs to be written to disk.
- Delivery: an HTTPS `PUT` of the PNG to `<ntfy server>/<topic>` carries the notification title, text, priority, and filename in ntfy-supported headers. Thus the image and text are part of the same notification. Non-ASCII/multiline text is encoded safely as an RFC 2047 header.
- When screenshot is off: the **original** JSON POST text-only notification is unchanged. Pushover sending and SMTP email are unchanged. Screenshot is **ntfy-only** in this build; enabling it with Pushover displays a clear error instead of silently losing the image.
- Images exceeding 14 MB are *not* uploaded. Switch to **Monitor at Cursor** or reduce max width to avoid ntfy.sh's usual 15 MB limit.

## Important privacy and reliability considerations

- **All visible screen content is sent to the configured ntfy server**. Your browser, internal apps, credential dialogs, IDs, customer or employer data may be included. Never send confidential or employer-owned information to an unapproved external service. Even private-looking ntfy.sh topics are not strong access control; use an approved, authenticated private instance for sensitive content.
- ntfy.sh attachments are typically available for **3 hours**, and phone behavior depends on ntfy app download settings.
- The screenshot may capture black or blank areas for secure desktops or protected windows. A locked desktop may not be readable.
- The last cursor position and PNG are captured when this action executes. Sequent's own visible windows may appear in the image if they cover the screen.
- A successful ntfy API response means the service accepted the message, not proof the phone received/displayed it.

## Verification

- Python syntax compilation passed.
- 8/8 screenshot test groups passed: PNG headers/CRC/marker, compressed rows, mock Win32 multi-monitor image capture, multiline/non-ASCII notification metadata, a single image+message PUT, fallback text-only POST, Pushover guard, and editor schema.
- 7/7 existing notification test groups passed, plus a Tkinter import/widget check under a virtual display.
- No real ntfy alert was sent during testing. Real Windows desktop capture and phone rendering **have not** been tested here.

Official protocol: https://docs.ntfy.sh/publish/#attach-local-file
