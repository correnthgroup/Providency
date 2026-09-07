# Providency browser connection

This Chromium extension connects an already signed-in browser to the local
Providency application. Supported targets are Vector Web and Telegram Web only.
The debugger permission is required for the Playwright CDP transport. The
browser displays its own debugging notification while connected.

For this development build, open your browser's extensions page, enable developer
mode, choose **Load unpacked**, and select this folder. This is a manual browser
installation, not an automatic modification of your profile. No browser restart,
remote-debugging flag, cookie export, or password transfer is needed.

Start Providency, click **Iniciar BOT**, generate a connection code, and paste it
in the extension popup. The port is shown beside the code. Open Vector and
Telegram Web in this browser and use the onboarding OK buttons to inspect them.
The code is session-scoped, used once, and never saved by the extension.

Disconnect or close Providency to detach. User tabs and login sessions remain
open. This increment identifies screens and destinations; it sends no messages
and starts no trading session. Firefox and Safari are not supported by this
Chromium extension.
