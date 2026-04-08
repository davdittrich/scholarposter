# Filtering Reference

scholarposter evaluates three filter rules against each toot before enrichment. Filters are configured per platform under `[[platforms.<name>.filters]]` in `config.toml`. See [configuration.md](configuration.md) for the field reference.

---

## Evaluation order

The three filter fields are checked in sequence:

1. `skip_hashtags`
2. `skip_content_types`
3. `require_hashtags`

The first failing check ends evaluation. Subsequent filters are not consulted. A toot that passes all three filters proceeds to enrichment and posting.

All hashtag comparisons are case-insensitive: `#Economics` and `#economics` are treated as the same tag.

---

## `skip_hashtags`

Rejects a toot when it contains **any** hashtag in the list.

```toml
[platforms.bluesky.filters]
skip_hashtags = ["NoBot", "NoShare"]
```

An empty list rejects nothing.

---

## `skip_content_types`

Rejects a toot that matches any of the specified content types.

```toml
[platforms.bluesky.filters]
skip_content_types = ["sensitive", "reblog"]
```

| Value | Posts rejected |
|-------|----------------|
| `"sensitive"` | Toots the author has marked as sensitive content |
| `"poll"` | Toots that contain a poll attachment |
| `"media_only"` | Toots with media attachments but no text body |
| `"reblog"` | Boosts of another account's post |

An empty list rejects nothing. Multiple values may be combined.

---

## `require_hashtags`

Rejects a toot when it contains **none** of the listed hashtags. A toot passes when it contains at least one.

```toml
[platforms.bluesky.filters]
require_hashtags = ["paper", "preprint", "doi"]
```

An empty list (the default) passes all toots.

---

## Hashtag rules

Hashtag rules are distinct from hashtag filters. A rule does not reject posts — it prepends a hashtag to the post text when a trigger condition is met.

```toml
[[platforms.bluesky.hashtag_rules]]
add_hashtag = "EconSky"
if_any_hashtag = ["Economics", "GameTheory", "Labor"]
```

When a post contains any hashtag listed in `if_any_hashtag`, the value of `add_hashtag` is prepended to the post text. Multiple rules are evaluated independently. All matching rules contribute their tag; the combined tags appear as a single space-separated prefix line before the post body.

For example, if two rules both fire, the post begins with:

```
#EconSky #AcademicTwitter
<original post text>
```

Hashtag matching in rules is also case-insensitive.
