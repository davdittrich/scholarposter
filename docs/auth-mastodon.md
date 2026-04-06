# Mastodon authentication

scholarposter authenticates with your Mastodon instance via OAuth — you click
"Authorize" in your browser and the token is saved automatically.

---

## Automated setup

```bash
scholarposter auth mastodon --config /path/to/config.toml
```

The command:
- Prompts for your instance URL (or reads `MASTODON_INSTANCE` from `.env`)
- Registers a "scholarposter" app with your instance
- Opens your browser to the Mastodon authorization page
- Captures the authorization code and exchanges it for an access token
- Saves the token to `pytooter_usercred.secret` and updates `config.toml`

On headless servers (no display), the command prints the authorization URL. Mastodon
displays the code directly on the page — paste it into the terminal prompt.

2FA works automatically — the browser handles it natively.

---

## Token lifecycle

Mastodon tokens do not expire unless you revoke them from Settings → Authorized apps.
If a token is revoked, scholarposter detects the 401 error and asks you to re-run
`scholarposter auth mastodon`.

There is no automatic token re-creation — the OAuth flow requires browser interaction.

---

## Troubleshooting

| Symptom | Fix |
|---------|-----|
| `Mastodon token revoked` | Re-run `scholarposter auth mastodon` |
| `Could not reach {instance}` | Check the URL — must be HTTPS |
| `Authorization timed out` | Try again; click "Authorize" within 120 seconds |
| `Token exchange failed` | The authorization code may have expired — try again |

---

## Security notes

- `.secret` files contain plain-text tokens. `install.sh` sets them to mode 600.
- Only `MASTODON_INSTANCE` is stored in `.env` — no email or password.
- The OAuth scope is `read` only — scholarposter cannot post to or modify your Mastodon account.
