# Mastodon authentication

scholarposter reads your toots using the [Mastodon.py](https://mastodonpy.readthedocs.io/) library. It needs an access token stored in a `.secret` file — this is a one-time setup.

---

## One-time setup

Run the following Python snippet once, in the directory where you plan to keep the credential files. Replace the values in angle brackets.

```python
from mastodon import Mastodon

# Step 1 — Register the application with your instance (creates a client credential file)
Mastodon.create_app(
    "scholarposter",
    api_base_url="https://<your.instance>",   # e.g. https://fediscience.org
    to_file="pytooter_clientcred.secret",
)

# Step 2 — Log in and save the user access token
mastodon = Mastodon(
    client_id="pytooter_clientcred.secret",
    api_base_url="https://<your.instance>",
)
mastodon.log_in(
    "<your-email@example.com>",
    "<your-password>",
    to_file="pytooter_usercred.secret",
)

print("Done. pytooter_usercred.secret written.")
```

This produces two files:

- `pytooter_clientcred.secret` — app registration (keep it, but scholarposter doesn't use it directly)
- `pytooter_usercred.secret` — your user access token (**keep this private**)

---

## Configure scholarposter

In `config.toml`, point `credentials_file` at the user credential file:

```toml
[mastodon]
instance = "https://fediscience.org"
credentials_file = "/home/user/scholarposter/pytooter_usercred.secret"
```

The path can be absolute or relative to the directory you run `scholarposter` from.

---

## Notes

- If your instance is not fediscience.org, change both `api_base_url` in the setup snippet and `instance` in `config.toml`.
- The `.secret` files contain plain-text tokens. `install.sh` sets them to mode `600` automatically. Do not commit them to version control.
- Mastodon.py user tokens do not expire unless you revoke them from your account settings (Settings > Authorized apps).
