# LinkedIn authentication

scholarposter posts to LinkedIn using the **Community Management API**. This requires an approved LinkedIn Developer app and a manually obtained OAuth 2.0 access token. The process takes about 15 minutes the first time.

---

## Step 1 — Create a LinkedIn Developer app

1. Go to [LinkedIn Developer Portal](https://www.linkedin.com/developers/apps) and click **Create app**.
2. Fill in App name (e.g. "scholarposter"), associate it with a LinkedIn Company Page (required), upload a logo, and agree to the terms.
3. On the **Products** tab, click **Request access** for **Community Management API**.
4. LinkedIn reviews requests manually. Approval usually takes 1-3 business days.

Once approved, the **Auth** tab will show the required scopes: `w_member_social`, `openid`, `profile`.

---

## Step 2 — Get your Client ID and Client Secret

On the **Auth** tab:

- Copy **Client ID** → `CLIENT_ID`
- Copy **Client Secret** → `CLIENT_SECRET`

Add a redirect URI. For the local capture trick below, use:

```
http://localhost:8080/callback
```

---

## Step 3 — Start a local redirect listener

Open a terminal and start a minimal HTTP server to catch the OAuth callback:

```bash
python3 -m http.server 8080
```

Leave this running.

---

## Step 4 — Authorize in the browser

Build the authorization URL and open it in your browser (replace `CLIENT_ID` with your actual value):

```
https://www.linkedin.com/oauth/v2/authorization?response_type=code&client_id=CLIENT_ID&redirect_uri=http%3A%2F%2Flocalhost%3A8080%2Fcallback&scope=openid%20profile%20w_member_social
```

Log in and click **Allow**. You will be redirected to `http://localhost:8080/callback?code=AQ...`.

The Python HTTP server will print the full request path. Copy the `code` value from the URL.

---

## Step 5 — Exchange the code for an access token

```bash
CLIENT_ID="your-client-id"
CLIENT_SECRET="your-client-secret"
CODE="AQ..."          # the code from Step 4

curl -s -X POST "https://www.linkedin.com/oauth/v2/accessToken" \
  -H "Content-Type: application/x-www-form-urlencoded" \
  -d "grant_type=authorization_code" \
  -d "code=$CODE" \
  -d "redirect_uri=http://localhost:8080/callback" \
  -d "client_id=$CLIENT_ID" \
  -d "client_secret=$CLIENT_SECRET"
```

The response JSON contains `access_token`. Copy it.

---

## Step 6 — Get your Member URN

```bash
TOKEN="AQX..."   # your access token from Step 5

curl -s -H "Authorization: Bearer $TOKEN" \
  "https://api.linkedin.com/v2/userinfo"
```

The response looks like:

```json
{"sub": "abcDEF123", "name": "Your Name", ...}
```

Your Member URN is `urn:li:person:<sub>`, e.g. `urn:li:person:abcDEF123`.

---

## Step 7 — Save credentials to `.env`

```bash
LINKEDIN_ACCESS_TOKEN="AQX..."
LINKEDIN_OWNER_URN="urn:li:person:abcDEF123"
```

---

## Token expiry

LinkedIn access tokens expire after **60 days**. There is no automatic refresh in scholarposter. When your token expires, repeat Steps 3–7 to get a new one.

You will know the token has expired when scholarposter logs `HTTP 401` errors for LinkedIn posts.
