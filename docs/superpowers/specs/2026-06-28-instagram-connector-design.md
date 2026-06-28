# Instagram organic-analytics connector — design

Date: 2026-06-28 · Project: Контур роста (data lake for infobusiness, ДЦП niche)
Status: approved design → implementation plan next

## 1. Goal

Pull the **maximum organic analytics** from the owner's Instagram professional
account into the lake, matching the existing ABC-Connector pattern
(`kontur/connectors/{vk,tiktok}`): `client` / `mapping` / `sync` + CLI,
idempotent upsert into `Channel` / `Content` / `ContentMetric` / `ChannelMetric`
(+ `RawRecord`, `SyncRun`, `OAuthToken`).

Scope of "organic analytics": posts & reels (reach, views, likes, comments,
shares, saves, reposts, watch-time, profile actions), account daily metrics
(reach, views, engagement, follows/unfollows, link taps) and audience
demographics (age/city/country/gender).

## 2. Locked decisions

1. **Access = Path B (Instagram API with Instagram Login)**, host
   `graph.instagram.com`. No Facebook Page, no App Review for the owner's own
   account (Standard Access). Trade-off accepted: no `story_insights` webhook,
   no ads-inclusive `total_*` metrics — both irrelevant to an organic mandate.
2. **Stories = v2.** v1 ships posts/reels + account + demographics. Story
   insights live only 24h and Path B has no webhook, so reliable stories need a
   separate high-frequency loop — deferred. The design leaves a clean seam (§11).
3. **Token store = existing `kontur/connectors/oauth.py` + `OAuthToken`.** Env
   var is bootstrap only; the DB row is the source of truth. (§8)
4. **Metric-semantics contract:** `ContentMetric` = lifetime-cumulative snapshot
   per `(content_id, snapshot_date)`; `ChannelMetric` = per-day value per
   `(channel_id, snapshot_date)`; `snapshot_date` in the account's timezone; all
   typed columns nullable; **empty API response → NULL, never 0**; unmapped
   metrics → `raw` JSONB; full insights payload → `RawRecord`. (§5)
5. **Backfill is urgent.** Account daily insights exist only for the trailing
   **90 days and are unrecoverable**; demographics have no history at all. v1
   ships a `backfill` command, run as soon as the token is in hand, and snapshots
   demographics on a weekly cadence from day one.

## 3. API facts (2026) and confidence

Verified this session against live Meta docs via curl (built-in WebFetch is
broken on these pages — Node/undici dies on the `/docs/`→`/documentation/`
HTTP/2 301 redirect; curl over HTTP/1.1 works). Full extract in the research
note kept with the implementation. Source dates noted; all HIGH unless marked.

- **Two APIs, same Graph metrics**, differ by host/login/scopes. Path B scopes:
  `instagram_business_basic`, `instagram_business_manage_insights`. [HIGH]
- **Media insights** `GET /<media_id>/insights` (doc updated 2026-06-18):
  per media `period=lifetime`; **stored 2 years**; 48h delay; organic only.
- **Account insights** `GET /<ig_user_id>/insights` (doc updated 2026-03-13):
  `period=day` time_series or `total_value`; **stored 90 days**;
  `online_followers` 30 days; demographics top-45; 48h delay.
- `impressions` deprecated: account-level removed 2025-04-21 (→ `views`);
  media-level removed for media created after 2024-07-02 (still returns for
  older media). [HIGH]
- Token: App-Dashboard "Generate token" → long-lived **60-day** token; refresh
  `GET graph.instagram.com/refresh_access_token?grant_type=ig_refresh_token&access_token=<LL>`
  → new 60-day token, refreshable when ≥24h old & not expired; **not refreshed
  in 60 days = dead permanently** (owner must re-issue). [HIGH]
- Account must be Business or Creator (`account_type` = `Business` |
  `Media_Creator`); personal accounts return no insights. [HIGH]
- API version pinned to a single config constant (current latest **v25.0**). [HIGH]
- Rate limits: Instagram BUC (business use case) per app/user; modest for one
  account but combinatoric call-splitting + backfill multiplies it. [MEDIUM]

### Media metric allow-list (by media product type)
| metric | FEED | REELS | STORY | typed col | notes |
|---|---|---|---|---|---|
| reach | ✓ | ✓ | ✓ | reach | estimated |
| views | ✓ | ✓ | ✓ | views | replaces impressions; "in development" |
| likes | ✓ | ✓ | | likes | |
| comments | ✓ | ✓ | | comments | |
| shares | ✓ | ✓ | ✓ | shares | |
| saved | ✓ | ✓ | | saves | |
| reposts | ✓ | ✓ | ✓ | → raw | |
| total_interactions | ✓ | ✓ | ✓ | → raw | "in development" |
| follows | ✓ | | ✓ | → raw | |
| profile_visits | ✓ | | ✓ | → raw | |
| profile_activity | ✓ | | ✓ | → raw | breakdown=action_type |
| ig_reels_avg_watch_time | | ✓ | | → raw | |
| ig_reels_video_view_total_time | | ✓ | | → raw | "in development" |
| reels_skip_rate | | ✓ | | → raw | estimated, "in development" |
| crossposted_views / facebook_views | | ✓ | (fb) | → raw | crosspost only |
| navigation | | | ✓ | → raw | breakdown=story_navigation_action_type (v2) |
| replies | | | ✓ | → raw | 0 for EU/JP creators → treat as NULL (v2) |
| link_clicks | | | ✓ | → raw | (v2) |
| impressions (legacy) | ✓ | | ✓ | → raw | only media created before 2024-07-02 |

### Account metric allow-list
- time_series `day`: `reach`, `views`.
- `total_value` (+breakdown): `accounts_engaged`, `total_interactions`,
  `likes`/`comments`/`saves`/`shares` (breakdown `media_product_type`),
  `reposts`, `replies`, `profile_links_taps` (breakdown `contact_button_type`),
  `follows_and_unfollows` (breakdown `follow_type`, ≥100 followers).
- demographics lifetime + timeframe: `follower_demographics`,
  `engaged_audience_demographics` (breakdown one of age/city/country/gender;
  ≥100 followers / ≥100 interactions).

## 4. Module layout

```
kontur/connectors/instagram/
  __init__.py    # docstring: organic IG analytics, Instagram-Login (Path B), single owner
  client.py      # InstagramClient
  mapping.py     # pure functions (no DB, no network)
  sync.py        # InstagramConnector(Connector) + backfill helper
```
Mirrors `vk/` exactly (live-API connector). Reuses `kontur/connectors/http.py`
(httpx factory w/ proxy/transport) and `oauth.py` (token persistence).

## 5. Data-model mapping

**Channel** `(platform="instagram", external_id=<ig_user_id>)`
- `title`=username · `url`=`https://instagram.com/{username}`
- `meta`={account_type, followers_count, follows_count, media_count, name,
  profile_picture_url, timezone}

**Content** `(channel_id, external_id=<media_id>)`
- `type`=`media_product_type` (FEED/REELS/STORY) · `title`=`caption[:500]`
- `url`=`permalink` · `published_at`=`timestamp`
- `metrics`=lifetime snapshot {reach, views, likes, comments, shares, saves}
- `raw`=media object fields (media_type, like_count, comments_count, thumbnail_url, …)

**ContentMetric** `(content_id, snapshot_date)` — lifetime-cumulative snapshot
(same convention as TikTok):
- typed: `views`←views · `reach`←reach · `likes`←likes · `comments`←comments ·
  `shares`←shares · `saves`←saved
- `raw`={ig_reels_avg_watch_time, ig_reels_video_view_total_time,
  reels_skip_rate, total_interactions, reposts, follows, profile_visits,
  profile_activity(+action_type), crossposted_views, facebook_views,
  impressions(legacy), media_product_type, period}

**ChannelMetric** `(channel_id, snapshot_date)` — per-day value. NOTE: this
model's typed columns are `followers, followers_gained, profile_views,
video_views, reach, likes, comments, shares`. To avoid the cross-platform
semantic collision (S2) — `video_views` means "video-only daily net" for TikTok —
IG account `views` (all-content) is NOT forced into `video_views`; it goes to
`raw`. `video_views` and `profile_views` stay NULL for IG (no clean account-level
equivalent in the 2026 API; account `profile_views` was dropped).
- typed: `followers`←followers_count(/me, point-in-time) · `reach`←reach(day) ·
  `likes`/`comments`/`shares`←day total_value ·
  `followers_gained`←derived from `follows_and_unfollows`
- `raw`={views(day, all-content), accounts_engaged, total_interactions, saves,
  follows_and_unfollows(+follow_type), profile_links_taps(+contact_button_type),
  reach/views breakdowns, online_followers,
  demographics{follower_demographics, engaged_audience_demographics},
  api_version, account_tz}

Hard mapper rules (from §2.4): absent metric → NULL; unmapped → raw; full
payload → `RawRecord`; `snapshot_date` in account tz.

## 6. Client (`client.py`)

`InstagramClient(token, *, api_base, api_version, session_factory, ...)`:
- `me()` → {user_id, username, account_type, followers_count, follows_count,
  media_count, name, profile_picture_url}
- `iter_media()` → paginate `/me/media?fields=id,media_type,media_product_type,
  caption,permalink,timestamp,like_count,comments_count,thumbnail_url`;
  follow `paging.cursors.after` to completion.
- `media_insights(media_id, product_type)` → pick allow-list per product type;
  split into compatible `(metric, breakdown)` groups; per-group try/except;
  skip story error code 10 ("<5 viewers"); skip carousel children.
- `account_insights(since, until, *, with_demographics)` → grouped calls by
  `(period, metric_type, breakdown)` signature; time_series day for reach/views;
  total_value for the rest; one call per demographic breakdown.
- `refresh_token()` → `/refresh_access_token` (Instagram-Login; no client_secret).
- Rate-limit handling: read `X-Business-Use-Case-Usage`; exponential backoff +
  bounded retries on BUC error codes (4/17/32/613), matching `VKClient` style.

Never batch heterogeneous metrics (one incompatible metric 400s the whole call).
Keep an explicit allow-list of known-good combos for the pinned version.

## 7. Sync (`sync.py`)

`InstagramConnector(Connector)`, `name = "instagram"`, implements
`ingest(session, run, stats)`:

1. **Token**: ensure a valid token via `oauth.py` (bootstrap from env on first
   run); refresh in a separate session and commit BEFORE ingest if the token is
   stale (§8); fail loudly if expired.
2. `me()` → land raw `account` → upsert `Channel`. Verify `account_type` is
   Business/Media_Creator (else fail loudly); record `followers_count` for the
   <100 gate.
3. `account_insights(trailing 3 days)` → upsert one `ChannelMetric` per
   account-tz day bucket (re-fetch trailing window each run so 48h-delayed data
   matures; idempotent overwrite). Demographics on a weekly gate → into that
   day's `ChannelMetric.raw`.
4. `iter_media()` → for each media: land raw `media`, upsert `Content`;
   `media_insights` → upsert `ContentMetric(content_id, today)`.
5. Populate `stats` {channel, media, content_metrics, channel_days,
   demographics, token_expires_at, groups_ok/groups_failed}.

Modes (CLI subcommands):
- `instagram sync` — daily incremental (steps above).
- `instagram backfill` — account insights over **90 days** in chunks + full
  media enumeration. Run once, ASAP after token issuance.
- `instagram refresh-token` — standalone, cron-driven; refresh when token age >
  threshold (e.g. 7 days) and before expiry; write new token + `expires_at`;
  alert at T-7 / T-1.

## 8. Token lifecycle

`OAuthToken(connector="instagram", access_token, refresh_token, expires_at, raw)`
already exists; `oauth.save_token(...)` writes in a separate session with
immediate commit (the docstring explicitly calls out Instagram's rotating
refresh token → must persist before the rollback-prone ingest). Connector logic:
- Load token: prefer `OAuthToken` row; if absent, seed from `INSTAGRAM_ACCESS_TOKEN`.
- Refresh decoupled from ingest via `instagram refresh-token` (cron). `ingest`
  also opportunistically refreshes-and-persists-first if stale, never letting an
  ingest rollback revert a freshly rotated token.
- Surface `expires_at` in every `SyncRun.stats`; a >60-day outage is
  unrecoverable by design — mitigated only by the standalone timer + alerting.

## 9. Config / env (`config.py` Settings)

- `INSTAGRAM_ACCESS_TOKEN` (bootstrap long-lived token)
- `INSTAGRAM_USER_ID` (optional; else discovered via `/me`)
- `instagram_api_base` default `https://graph.instagram.com`
- `instagram_api_version` default `v25.0`
- `instagram_timezone` default = owner's tz (for day buckets) — configurable
- DB URL & token store reuse existing infra; relay/proxy via existing `http.py`.

## 10. Owner runbook (`docs/instagram-token-runbook.md`, created in impl)

1. Instagram app → Settings → switch account to **Business** or **Creator**.
2. developers.facebook.com/apps → create app, type **Business**.
3. Add product **Instagram** → "Set up Instagram business login".
4. Add the owner's IG account to the app (Standard Access — no App Review).
5. Click **Generate token** next to the account → log into Instagram → copy the
   60-day long-lived token.
6. Hand over **only that token** (user_id is discovered via `/me`).
7. We persist it in `OAuthToken` and auto-refresh on a cron.

## 11. Out of scope / v2 seam

- **Stories** (`instagram sync-stories`): high-frequency loop calling
  `iter_stories()` + `media_insights(STORY)`, MAX-merge per `(content_id, day)`,
  to catch insights near the end of the 24h window. `STORY` is already in the
  media allow-list; the v2 command reuses the client untouched.
- Ads-inclusive `total_*` and `story_insights` webhook → only if we ever move to
  Path A; client `base_url`/scopes/allow-list are config so A↔B is a config flip.

## 12. Testing (`tests/`, flat `test_instagram_*.py` + `instagram_fake.py`)

- `test_instagram_mapping.py` — pure mapping over recorded fixtures: each media
  type; demographics breakdown parsing; empty→NULL (no fabricated 0); EU/JP
  replies→NULL; <100-followers gate; legacy impressions.
- `test_instagram_client.py` — httpx-mocked: media pagination cursors; insights
  call-splitting & per-group failure isolation; token refresh; BUC retry.
- `test_instagram_sync.py` — in-memory SQLite: idempotency (double run → same
  rows), account-tz day bucketing, trailing-window maturation, backfill window,
  token persisted before ingest. Mirror `test_vk_sync.py` structure; reuse a
  fake client à la `vk_fake.py`.

## 13. To confirm during planning

- Exact `OAuthToken` / `config.Settings` field wiring (read at impl time).
- Account timezone source: `/me` does not expose tz on Path B → default to
  `instagram_timezone` config; revisit if a tz field is available.
- `followers_gained` derivation from `follows_and_unfollows` follow_type split.
