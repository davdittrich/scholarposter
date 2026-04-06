# LinkedIn authentication

scholarposter automates LinkedIn OAuth 2.0 — you run one command, click "Allow"
in your browser, and token management (storage and expiry warning) is
handled automatically.

---

## One-time setup: create a LinkedIn Developer app

This manual step takes about 15 minutes. You only do it once.

1. Go to [LinkedIn Developer Portal](https://www.linkedin.com/developers/apps) and click **Create app**.
2. Fill in App name (e.g. "scholarposter"), associate it with a LinkedIn Company Page, upload a logo, and agree to the terms.
3. On the **Products** tab, request access to:
   - **Share on LinkedIn** or **Community Management API** (for posting — name varies by app type)
   - **Sign In with LinkedIn using OpenID Connect** (required for the userinfo endpoint that resolves your member URN)

   If you already have a LinkedIn app with these products approved, you can reuse it —
   just add the redirect URI and copy the Client ID/Secret.
4. LinkedIn reviews requests manually — approval usually takes 1-3 business days.
5. On the **Auth** tab, add this **Authorized Redirect URI**:
   ```
   http://localhost:8080/callback
   ```
6. Copy **Client ID** and **Client Secret** and add them to your `.env` file:
   ```bash
   LINKEDIN_CLIENT_ID=your-client-id
   LINKEDIN_CLIENT_SECRET=your-client-secret
   ```

---

## Authorize scholarposter

```bash
scholarposter auth linkedin --config /path/to/config.toml
```

This command:
- Opens your browser to LinkedIn's authorization page (or prints the URL on headless servers)
- Captures the OAuth callback automatically
- Exchanges the code for an access token (60-day lifetime)
- Looks up your LinkedIn Member URN
- Stores all credentials in `.env`

On headless servers (no display), the command prints the authorization URL and prompts you to paste the callback URL from your browser.

---

## Token lifecycle

| Token | Lifetime | Management |
|-------|----------|------------|
| Access token | 60 days | scholarposter warns 7 days before expiry via notifications |

No auto-refresh — LinkedIn restricts refresh tokens to partner programs. When the
token expires, re-run `scholarposter auth linkedin`.

---

## Troubleshooting

| Symptom | Fix |
|---------|-----|
| `LinkedIn requires OAuth setup` | Run `scholarposter auth linkedin` |
| `LinkedIn: token expired` | Access token expired (60-day lifetime). Re-run `scholarposter auth linkedin` |
| `Port 8080 is in use` | Use `--port 9090` (update redirect URI in LinkedIn app to match) |

Check token status: `scholarposter status`

## Migration from older versions

Versions before commit `ae896f8` managed `LINKEDIN_REFRESH_TOKEN` and `LINKEDIN_REFRESH_EXPIRES_AT` in `.env`. LinkedIn restricts refresh tokens to partner programs, so scholarposter no longer requests or stores them. If your `.env` still has these lines, they cause no harm (no code reads them) but you can remove them. The current managed variables are `LINKEDIN_ACCESS_TOKEN`, `LINKEDIN_OWNER_URN`, and `LINKEDIN_TOKEN_EXPIRES_AT`.
