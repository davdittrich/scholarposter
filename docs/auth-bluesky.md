# Bluesky authentication

scholarposter authenticates to Bluesky with an app password — no OAuth flow required.

## Create an app password

1. Log into [bsky.app](https://bsky.app).
2. Open **Settings → Privacy and Security → App Passwords**.
3. Click **Add App Password** and name it (e.g. `scholarposter`).
4. Copy the generated password. You cannot retrieve it later.

## Configure credentials

Add both values to your `.env` file:

```bash
BLUESKY_EMAIL=your@email.com
BLUESKY_PASSWORD=xxxx-xxxx-xxxx-xxxx
```

## Security notes

- App passwords grant full account access. Treat them like your main password.
- Store `.env` with restricted permissions: `chmod 600 .env`
- scholarposter loads credentials via `python-dotenv` at startup. They never appear in config files or logs.
