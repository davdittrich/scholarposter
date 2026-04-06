# LinkedIn authentication

scholarposter automates LinkedIn OAuth 2.0 — you run one command, click "Allow"
in your browser, and token management (storage, refresh, expiry warning) is
handled automatically.

---

## One-time setup: create a LinkedIn Developer app

This manual step takes about 15 minutes. You only do it once.

1. Go to [LinkedIn Developer Portal](https://www.linkedin.com/developers/apps) and click **Create app**.
2. Fill in App name (e.g. "scholarposter"), associate it with a LinkedIn Company Page, upload a logo, and agree to the terms.
3. On the **Products** tab, request access to:
   - **Share on LinkedIn** or **Community Management API** (for posting — name varies by app type)
   - **Sign In with LinkedIn using OpenID Connect** (for refresh tokens)

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
- Exchanges the code for access + refresh tokens
- Looks up your LinkedIn Member URN
- Stores all credentials in `.env`

On headless servers (no display), the command prints the authorization URL and prompts you to paste the callback URL from your browser.

---

## Token lifecycle

| Token | Lifetime | Management |
|-------|----------|------------|
| Access token | 60 days | Auto-refreshed before each post when within 24 hours of expiry |
| Refresh token | 365 days | scholarposter warns 7 days before expiry via configured notifications |

After the refresh token expires, re-run `scholarposter auth linkedin`.

---

## Troubleshooting

| Symptom | Fix |
|---------|-----|
| `LinkedIn requires OAuth setup` | Run `scholarposter auth linkedin` |
| `LinkedIn: DISABLED (auth expired)` | Refresh token revoked or 3+ refresh failures. Re-run `scholarposter auth linkedin` |
| `Port 8080 is in use` | Use `--port 9090` (update redirect URI in LinkedIn app to match) |
| `LinkedIn did not return a refresh token` | Enable "Sign In with LinkedIn using OpenID Connect" on your app's Products tab |

Check token status: `scholarposter status`
