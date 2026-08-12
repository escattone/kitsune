---
title: Mozilla Connect (Khoros) API
---

# Mozilla Connect (Khoros) API

[Mozilla Connect](https://connect.mozilla.org) runs on Khoros Communities. This
page documents its API as we actually found it, so we can pull Connect content
into BigQuery.

`scripts/khoros_export.py` is the exporter built on top of this.

Khoros is SaaS, so there is no database schema to read. What we have is the
**LiQL collection model** — the API's view of the data. Everything below was
probed against the live instance anonymously on 2026-08-12, so it reflects what
we can actually reach, not just what Khoros documents.

Endpoint: `GET https://connect.mozilla.org/api/2.0/search?q=<LiQL>`
No credentials needed for public content, post bodies included.

## Collection map

| Collection | Rows | Anonymous? | What it gives you |
|---|---|---|---|
| `messages` | 92,383 | ✅ | Every post: body, author, times, views, state. The core table. |
| `boards` | 4 | ✅ | The four forums, with settings and lifetime view counts. |
| `nodes` | 6 | ✅ | Container tree above boards, including one group hub. |
| `ranks` | 36 (14 active) | ✅ | The reputation ladder and staff badges. |
| `images` | 7,204 | ✅ per message | Uploaded images, URLs at seven sizes. Bulk sweeps truncate — see below. |
| `labels` | — | ✅ per message | Curated taxonomy (`Thunderbird`, `Mobile-Android`). Thread openers only. |
| `tags` | — | ✅ per message | Free-text tags (`android`). **Replies carry these too.** |
| `kudos` | — | ✅ per message | Individual votes: who, when, weight. |
| `revisions` | — | ✅ per message | Edit metadata only — no historical body text. |
| `users` | — | ❌ **0 rows** | Locked to anonymous callers. Use `author.*` instead. |
| `categories` | 0 | ✅ | Empty on this instance. |
| `custom_tags` | — | ✅ | Readable, zero rows in every sample. |
| `attachments` | — | ✅ | Readable, zero rows in every sample. |
| `ratings` | — | ✅ | Readable, zero rows in every sample. |
| `videos` | — | ✅ | Readable, zero rows in every sample. |
| `threads`, `notes` | — | — | Not valid collection names. |

### Volumes per board

| Board | Slug | Style | Topics | Replies | `count(*)` |
|---|---|---|---|---|---|
| Ideas | `ideas` | idea | 10,003 | 43,375 | 53,779 |
| Discussions | `discussions` | forum | 8,726 | 25,332 | 34,435 |
| Firefox Labs | `Labs` | forum | 221 | 4,723 | 4,948 |
| Community | `community` | forum | 3 | 0 | 3 |
| **Total** | | | **18,953** | **73,430** | **93,165** |

⚠️ **Unresolved count discrepancy.** Topics + replies = 92,383, which matches
`SELECT count(*) FROM messages` exactly. But per-board `count(*)` sums to 93,165
— 782 higher. So 782 messages have no `depth` value and are invisible to any
depth-filtered query. Image comments are the likely culprit (the `images`
collection references `messages WHERE ... AND is_image_comment = true`), but
`is_image_comment` can't be used as a filter to confirm it. Practical upshot:
don't use `count(*)` as a completeness check, and dedupe the export on
`message_uid`. A full cursor sweep of one board will settle the real number.

## messages — 50 top-level fields

`SELECT *` returns 41 of these. Nine more are selectable but **absent from
`SELECT *`**, marked † below — including `parent` and `status`, both of which
matter. Don't treat `SELECT *` as the field list.

### Identity and position in the tree

| Field | What it is |
|---|---|
| `id` | Message ID. Matches `message_uid` in the BigQuery event tables. |
| `type` | Always `message` |
| `message_type` | `forum_topic_message`, `forum_reply_message`, `idea_topic_message`, … |
| `depth` | 0 = opened the thread, >0 = a reply. Null on 782 messages (see above). |
| `href` / `view_href` | API path, and the real public URL of the post |
| `board` | → boards: `id`, `title`, `conversation_style`, `href`, `view_href` |
| `conversation` | → thread: `id`, `style`, `thread_style`, `messages_count`, `solved`, `last_post_time` |
| `topic` | → the thread's opening post |
| `parent` † | → the post this replies to. **The real threading key.** |

### Content

`subject` · `body` (HTML) · `teaser` (usually empty) · `search_snippet` ·
`language` · `seo_title` † (empty) · `canonical_url` † (empty)

### Time

`post_time` · `post_time_friendly` · `last_publish_time` †

For edit time use `current_revision.last_edit_time`. Plain `last_edit_time` is
**not valid** and rejects the whole query.

### People

| Field | What it is |
|---|---|
| `author` | → users. See the users section for the reachable subset. |
| `current_revision` | `id`, `revision_num`, `last_edit_time`, `last_edit_author` |

### Engagement

| Field | What it is |
|---|---|
| `metrics.views` | View count. Bare `views` is **not valid**. |
| `popularity` | Khoros's decaying score. Often negative. |
| `kudos.sum(weight)` | Total kudos on the post, free inline |
| `replies.count(*)` | Reply count |
| `conversation.messages_count` | Posts in the whole thread |
| `conversation.solved` | Thread has an accepted solution |

### State — 12 fields

| Field | What it is |
|---|---|
| `status` † | **Ideas workflow state**: `{key, type_key, name, completed}` |
| `moderation_status` | `approved`, … |
| `visibility_scope` | `public`. Only appears in `SELECT *`; naming it directly is rejected. |
| `is_solution` / `can_accept_solution` | Accepted-answer flags |
| `read_only` · `edit_frozen` · `is_promoted` · `placeholder` | Locks and pins |
| `excluded_from_kudos_leaderboards` · `include_hidden_messages` | |
| `is_image_comment` † | Marks image comments. Selectable, but **not usable as a filter**. |

### Sub-queries — one request each

`labels` · `tags` · `custom_tags` · `kudos` · `images` · `videos` ·
`attachments` · `ratings` · `replies` · `revisions` † · `descendants` † ·
`ancestors` †

Each supports a count without fetching the rows — `labels.count(*)`,
`tags.count(*)`, `images.count(*)`, etc. Cheap way to know what's there before
spending a request.

### Skip

`user_context` — per-caller state (`can_reply`, `read`). Meaningless in an export.
`solution_data` — always empty.

## Idea status

`status` on an idea topic:

```json
{"type": "message_status", "key": "new", "type_key": "idea",
 "name": "New idea", "completed": false}
```

In 400 recent idea topics: 399 `New idea`, 1 `Delivered`. The interesting values
live on older ideas. Since Ideas is the largest board, this is the field that
answers "which ideas shipped".

## boards — 33 fields

Four rows. Note the capital L on `Labs`; slugs are case-sensitive.

Worth having: `id`, `title`, `short_title`, `description`, `conversation_style`
(`forum` / `idea`), `creation_date`, `views` (lifetime), `position`, `depth`,
`hidden`, `language`, `rating` (`kudos`), `allowed_labels`
(`predefined-only` on Ideas), `require_thread_root_label`, `comments_enabled`,
`announcements`, `date_pattern`, `skin`, `view_href`.

Sub-queries: `messages` (all posts) and `topics` (`... AND depth = 0`).

Lifetime views: Discussions 388.7M, Ideas 262.7M, Firefox Labs 59.6M,
Community 3.1K.

## nodes — 20 fields

Six rows — the container tree. `boards` is a **subset** of `nodes`: nodes covers
every container type, boards only the ones holding conversations.

```
Mozilla Connect          (community)   711.0M views
├── Ideas                (board)       262.7M
├── Discussions          (board)       388.7M
├── Community            (board)         3.1K
├── Firefox Labs         (board)        59.6M
└── Group Hub Test       (grouphub)         0
```

The group hub appears in `nodes` but **not** in `boards`. No categories exist, so
the tree is flat.

Fields: `id`, `title`, `short_title`, `description`, `node_type` (`community` /
`category` / `board` / `grouphub`), `depth`, `position`, `hidden`,
`creation_date`, `views`, plus `ancestors` / `children` / `messages` / `topics`
sub-queries.

⚠️ **IDs differ between the two collections.** A board is `board:ideas` in
`nodes` but `ideas` in `boards`, and messages reference the `boards` form. Strip
the `board:` prefix to join them.

## ranks — 36 rows, 14 active

The tier shown next to a name on every post. 22 ranks are stock Khoros defaults
marked `rank_status: deleted` (Esteemed Contributor III, Visitor II, …) — setup
leftovers, ignore them.

The 14 active ranks are two systems in one field. Lower `position` = higher standing.

**Assigned — staff badges tied to a role (`formula_enabled: false`):**

| Pos | Rank | Granted by |
|---|---|---|
| 0 | Community Manager | `hasRole("Administrator")` |
| 1 | Moderator | `hasRole("Moderator")` |
| 2 | Employee | `hasRole("Employee")` |
| 3 | Thunderbird Team | `hasRole("Thunderbird Team")` |
| 4 | Khoros | `hasRole("Khoros")` |

**Earned — automatic ladder (`formula_enabled: true`), thresholds not exposed:**

Positions 5–12: MVP, All-Star, Leader, Collaborator, Contributor,
Familiar face, Making moves, Strollin' around.

Position 13 is `New member`, the floor.

Fields: `id`, `name`, `position`, `bold`, `color`, `rank_status`,
`formula_enabled`, `simple_criteria`, `icon_left`.

Distribution across 276 recent authors: Making moves 169, New member 87,
Strollin' around 15, Employee 2, Community Manager 1, Contributor 1,
"Not applicable" 1 (that last one isn't in the ranks collection — probably a
deleted account). So rank is a crude activity proxy, but the top five tiers
cleanly separate Mozilla staff from community members.

## images — 24 fields

`id`, `title`, `description`, `width`, `height`, `size`, `upload_time`,
`moderation_status`, `visibility`, `owner`, `album`, and URLs at seven sizes
(`tiny_href` → `original_href`). Links back via the `messages` sub-query.

Image files themselves are public — fetching an `original_href` returns the
bytes with no authentication.

⚠️ **Bulk sweeps of this collection are unreliable.** `count(*)` reports 7,204,
but a cursor sweep stops early and where it stops depends on the sort order:

| Sweep | Rows returned | Years covered |
|---|---|---|
| `ORDER BY upload_time ASC` | 955 | all 2022 |
| `ORDER BY upload_time DESC` | 1,967 | 2025 and 2026 |
| Overlap between them | 0 | — |

Both stop without handing back a cursor, and an image attached to a live,
currently-visible message appeared in neither. So this is truncation, not a
filter hiding deleted content.

**Get images per message instead.** Two reliable routes, in order of cost:

1. **Read the body HTML.** Khoros inlines images as ordinary `<img>` tags
   pointing at `/t5/image/serverpage/image-id/<id>/`. Free — the body is already
   in the message sweep — and it covers the large majority.
2. **Query the message.** `SELECT * FROM images WHERE messages.id = '<msg>'` for
   anything the body missed.

`images.count(*)` on a message tells you how many Khoros-hosted images it has,
so you can compare against what the body gave you and only spend a request on
the shortfall. Across the whole community that's roughly 37 messages.

Note that `images.count(*)` counts **only Khoros-hosted images**. Bodies also
link images on imgur, githubusercontent and similar, which never appear in this
collection — so a post can legitimately show more `<img>` tags than its count.

## labels — 6 fields

`id` (the label text, e.g. `Mobile-Android`), `text`, `time` (first use), `href`,
`type`, `messages` sub-query.

**Access is awkward.** Only two forms work:
- `SELECT * FROM labels WHERE messages.id = '<msg>'` — per message
- `SELECT id FROM messages WHERE labels.text = '<label>'` — reverse, only if you
  already know the label text

All of these are rejected: `SELECT * FROM labels`, `WHERE board.id`,
`WHERE node.id`, `WHERE messages.board.id`, and selecting `labels.id` inline on
messages. The Ideas board declares `allowed_labels: predefined-only`, so a fixed
vocabulary exists, but no query form returns it.

## tags — 5 fields

`id` (numeric), `text`, `href`, `type`, `messages` sub-query. Same access
limitation as labels: reachable per message, or in reverse via
`SELECT id FROM messages WHERE tags.text = '<tag>'`.

**Unlike labels, replies carry tags.** Sweeping one board found 195 tags across
117 messages, and 167 of them — 86% — were on replies rather than thread
openers. Anything that filters to topics will miss most of the tag data.

**The per-message query is complete**, which is worth stating explicitly given
how `images` behaves. Every message tested returned exactly as many tags as
`tags.count(*)` claimed, including one with 10:

| Message | `tags.count(*)` | Rows returned |
|---|---|---|
| 118320 | 7 | 7 |
| 110700 | 4 | 4 |
| 105542 | 3 | 3 |
| 134625 | 10 | 10 |

## kudos — 7 fields

`id`, `weight`, `time`, `user` (id, login, view_href), `message`, `href`, `type`.

Per message only: `SELECT * FROM kudos WHERE message.id = '<msg>'`. The total is
free inline via `kudos.sum(weight)`, so fetch these rows only if you need to
know *who* voted. Note `kudos.count(*)` is **not** valid — only
`kudos.sum(weight)`. Every weight seen so far is 1, so the sum doubles as a count.

**Replies carry the overwhelming majority.** In one board, 3,898 replies had
kudos against 38 topics — 98.6% of the 31,047 total. Anything that assumes kudos
live on thread openers will miss nearly all of them.

**The per-message query is complete and pages properly**, unlike `images`. A
reply with 1,042 kudos returned 1000 rows plus a cursor, then 42 more —
1,042 exactly, matching `kudos.sum(weight)`.

Fetching kudos detail for the whole community is expensive. Share of messages
carrying at least one kudo, by board:

| Board | Sample | With kudos |
|---|---|---|
| Firefox Labs | whole board, 4,944 | 80% |
| Discussions | newest 1,000 | 38% |
| Ideas | newest 1,000 | 23% |

The Discussions and Ideas figures are drawn from the newest posts and so
understate the true rate, since kudos accumulate over time. Expect somewhere
between 30,000 and 75,000 requests for a full kudos export — a few hours.

## revisions — 4 fields

`id` (e.g. `134625_1`), `revision_num`, `last_edit_time`, `last_edit_author`.

Per message: `SELECT * FROM revisions WHERE message.id = '<msg>'`. **No
historical body text** — you learn that a post was edited and by whom, not what
it said before.

## Rate limiting

Khoros publishes no rate limit for the Community API and sends no rate-limit
headers. A 429 is the only signal you get.

**It throttles more often than you would expect.** On a long run at roughly 4
requests a second, about **one request in seven** came back 429 — 414 of 2,914
requests during a labels export. Short runs barely see one: a 95-page message
sweep and several hundred ad-hoc queries at the same rate went through
untouched. That pattern points to a rolling quota over some window rather than a
cap on instantaneous rate, though the actual numbers aren't published.

**Every 429 recovered on the first retry**, one second later. None ever needed a
second attempt. So the right response is a brief wait and another go, not
slowing the whole job down.

⚠️ **Khoros sends `Retry-After: 0` with its 429s.** Take that at face value and
your retries fire instantly, burn every attempt in milliseconds, and the job
dies — which is exactly what happened to a first attempt at the labels export.
Treat `Retry-After` as a floor to raise the wait, never to lower it:

```python
wait = min(max(float(retry_after), 2 ** attempt), 300)
```

**Image downloads are governed separately.** 14,586 files pulled at six
concurrent workers, roughly 12 requests a second, produced zero 429s. Those
come through Cloudflare (`cf-cache-status` is present on the response) while the
API does not, so CDN throughput says nothing about what the API will tolerate.

## users — blocked, but reachable through `author.*`

`SELECT * FROM users` returns HTTP 200 with **zero rows**, even for a targeted
`WHERE id = '135643'`. Anonymous callers cannot read the collection.

Tested across 276 distinct authors from 300 recent topics:

| Available | Coverage |
|---|---|
| `author.id` | 276/276 |
| `author.login` | 276/276 |
| `author.rank.*` (`id`, `name`, `position`, `bold`, `color`) | 276/276 |
| `author.online_status` | 276/276 |
| `author.deleted` | 276/276 |
| `author.view_href` / `href` | 275/276 |
| `author.last_visit_time` | 271/276 |
| `author.avatar.message` | ✅ |
| `author.messages.count(*)` | ✅ lifetime total |
| `author.topics.count(*)` | ✅ lifetime total |
| `author.solutions_authored.count(*)` | ✅ |
| `author.albums.count(*)` | ✅ |

**Withheld — 0/276, so blocked rather than coincidence:** `email`,
`first_name`, `last_name`, `biography`, `location`, `sso_id`.

**Rejected outright:** `registration_time`, `roles`, `nickname`,
`replies.count(*)`, `kudos_received.count(*)`, `kudos_given.count(*)`,
`badges.count(*)`.

The activity counts are real lifetime totals, and include boards we can't see —
for `kelimuttu` the API reports 38 messages while only 33 are visible in the four
public boards.

Aggregates need the function form: `author.solutions_authored.count(*)` works,
`author.solutions_authored.count` does not.

Full user profiles need an API app with admin rights
(Community Admin → System → API Apps, or profile icon → Dev Tools → API Apps).

## Quirks worth remembering

1. **`SELECT *` is not the field list.** Nine message fields are selectable but
   omitted from it, including `parent` and `status`.
2. **One bad field kills the whole query** with `Invalid query syntax` and no hint
   which field. Add fields one at a time.
3. **`last_edit_time` and `views` are not valid** on messages. Use
   `current_revision.last_edit_time` and `metrics.views`.
4. **`visibility_scope` only appears in `SELECT *`.** Naming it is rejected.
5. **`is_image_comment` is selectable but not filterable.**
6. **`IN (...)` is not supported.** No batching of per-message lookups.
7. **`count(*)` works on** `messages`, `boards`, `nodes`, `images`, `ranks` but is
   rejected on the constrained collections.
8. **`LIMIT` caps at 1000 — and omitting it silently gives you 25.** There is no
   "return everything" default. A message with 79 tags answers
   `SELECT * FROM tags WHERE messages.id = '37391'` with 25 rows and a cursor,
   and nothing about the response says it was truncated. Always pass a LIMIT.
   Page further with `CURSOR '<next_cursor>'`; `OFFSET` breaks past ~2000 rows.
9. **Only labels are thread-opener-only. Tags and kudos are not.** In one board,
   86% of tags and 98.6% of kudos sat on replies. Drive off `tags.count(*)`,
   `labels.count(*)` and `kudos.sum(weight)` per message rather than inferring
   anything from `depth`.
10. **Slugs are case-sensitive** — `Labs`, not `labs`.
11. **Timestamps in LiQL need a colon in the offset**:
    `2024-08-07T00:00:00.000+00:00`. `strftime('%z')` gives `+0000` and is rejected.

## How this compares to the BigQuery event tables

The API is better for content and current state. The event log keeps things the
API never had.

| | API | BigQuery event log |
|---|---|---|
| Post text | ✅ | ❌ |
| Reply threading (`parent.id`) | ✅ | ❌ |
| Idea workflow status | ✅ current | ✅ as change events |
| Views | ✅ current total | ✅ every individual event |
| Kudos | ✅ total + who | ✅ as events, 2 years |
| History | ✅ all of it | ⚠️ 2024-08-07 onward only |
| Deleted threads | ❌ gone | ✅ still recorded |
| Boards | ❌ only 4 public | ✅ all 19, incl. moderation and media |
| Visitor / visit IDs | ❌ | ✅ |
| Geography, device | ❌ | ✅ |
| Referrer host / URL | ❌ | ✅ |
| Search terms and results | ❌ | ✅ |

Neither is a superset. The API returns 18,953 topics; the event log shows 26,061
distinct threads across the same four boards — a gap of ~7,400 threads that have
since been deleted, merged or made private. The event log also covers 15 boards
the API won't show at all (Public Media at 9,162 threads, Private Media at 2,941,
Abuse Reports, Filter Notifications, Moderation Archive, and the rest).
