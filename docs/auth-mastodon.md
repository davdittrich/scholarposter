# Mastodon authentication

scholarposter registers an app and logs in to your Mastodon instance automatically.

---

## Automated setup

```bash
scholarposter auth mastodon --config /path/to/config.toml
```

The command prompts for your instance URL, email, and password (hidden input). It:
- Registers a "scholarposter" app with your instance
- Logs in and saves an access token to `pytooter_usercred.secret`
- Stores credentials in `.env` for automatic token re-creation
- Updates `config.toml` with the instance URL and credential file path

For scripted setup (CI/automation), set `MASTODON_INSTANCE`, `MASTODON_EMAIL`, and `MASTODON_PASSWORD` in `.env` before running the command — it runs non-interactively when all three are present.

---

## Token lifecycle

Mastodon user tokens do not expire unless you revoke them from Settings → Authorized apps. If a token is revoked, scholarposter detects the 401 error and automatically re-creates the token using saved credentials from `.env`.

If `.env` credentials are not available (e.g., you chose not to store the password), scholarposter prints an error message and asks you to re-run `scholarposter auth mastodon`.

---

## 2FA accounts

If your account has two-factor authentication enabled, `scholarposter auth mastodon` cannot log in automatically. Instead:

1. Go to your Mastodon instance → Settings → Development → New Application
2. Set the application name (e.g. "scholarposter") and create it
3. Copy the access token
4. Create `pytooter_usercred.secret` manually with the token as its only content
5. Set `credentials_file` in `config.toml` to point to this file

---

## Security notes

- `.secret` files contain plain-text tokens. `install.sh` sets them to mode 600.
- If you store `MASTODON_PASSWORD` in `.env`, it enables unattended token re-creation. For higher security, omit it — you will need to re-run `auth mastodon` manually if the token is ever revoked.
- The password is cleared from process memory immediately after use.
