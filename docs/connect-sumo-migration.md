---
title: Migrating Mozilla Connect into SUMO
---

# Migrating Mozilla Connect into SUMO

Working notes, not a plan. Everything here is measured rather than estimated —
the queries behind it are in [Mozilla Connect (Khoros) API](mozilla-connect.md),
which is the reference page for the data itself.

Figures are from 2026-10-02.

## Scope

**Ideas and Discussions only**, and `DEFAULT_BOARDS` in the export script now
reflects that. Firefox Labs and Community are two more *forum*-style boards, not
different content types, and dropping them costs 5% of messages and 8% of
posters. Pass `--board <slug>` to override — that replaces the default list
rather than adding to it, and slugs are case-sensitive (`Labs`, not `labs`).

| | In scope | Whole export |
|---|---:|---:|
| Messages | 89,439 | 94,439 |
| Distinct posters | 37,231 | — |
| Everyone who has ever posted (`message_authors`) | — | 40,491 |
| Kudos | 186,856 | 217,932 |
| Messages carrying a kudo | 26,589 | 30,536 |
| Distinct images | 4,760 | 4,969 |

Images, attachments and everything else the export already produces comes
across as-is.

Two boards, two styles: Ideas is `conversation_style = idea`, Discussions is
`forum`.

## Identity is the hard part

SUMO accounts are backed by Mozilla accounts, and we cannot create those for
anyone. But SUMO already understands an account that exists without one —
`Profile.fxa_uid` is nullable, and there is an `is_fxa_migrated` flag left over
from SUMO's own migration.

### Connect sits behind Auth0, not Mozilla accounts directly

A Connect user's `sso_id` is an Auth0 identifier, `<connection>|<provider>|<id>`.
It is **not** the FxA UID — but for people who signed in with a Mozilla account,
the FxA UID is the last segment:

```
oauth2|firefoxaccounts|4eda450cac5d4082994c46043e8f1a9d   → strip prefix, matches Profile.fxa_uid
google-oauth2|100214065820201232677                       → no FxA UID
email|674591bb7fa6d023f3a5e59a                            → no FxA UID
github|73586510                                           → no FxA UID
ad|Mozilla-LDAP|<username>                                → staff
```

`sso_id` is withheld from anonymous API callers. An admin's own logged-in
browser session can read it, which is how these numbers were gathered.

### Only 42% of participants can be matched

From a stratified sample of 500 in-scope posters, weighted back to the true
group sizes:

| Connection | Share | Estimated people |
|---|---:|---:|
| `oauth2\|firefoxaccounts` | **42%** ±4 | ~15,600 |
| `google-oauth2` | 33% | ~12,250 |
| `email` (passwordless) | 19% | ~7,050 |
| `github` | 6% | ~2,230 |
| `ad\|Mozilla-LDAP` | ~0.1% | ~40 |

Email is present for every user sampled (500/500).

**The two boards differ, and not by chance.** Ideas posters 47% ±6.5, Discussions
posters 35% ±6.2 — a 12-point gap at p ≈ 0.01. The Discussions half of the
migration leans harder on whatever fallback we pick.

### How claiming already works

From `kitsune/users/auth.py`, when someone signs in with a Mozilla account:

1. Match `Profile.fxa_uid` against the token's `uid` (line 165).
2. Failing that, match `User.email` case-insensitively (library default).
3. Failing that, create a new account — and any imported content stays stranded
   on a row nobody owns.

So populating `fxa_uid` at import time makes step 1 fire on first login, with no
changes to the login flow.

### Proposed approach: store no email addresses at all

| Connect user | Action |
|---|---|
| FxA match, already has a SUMO account | Link. No new rows. |
| FxA match, no SUMO account | Create with `fxa_uid` set, email empty |
| No FxA match (58%) | No account. Keep the Connect display name as text. |

SUMO fills the email in itself on first login, straight from the FxA claims, so
the import never needs to supply one.

For the 58%, an imported account is claimable only if the person creates a
Mozilla account using the same address they used on Connect. Most will not.
Importing ~21,600 accounts that stay dormant forever buys little and costs a
great deal.

What this avoids:

- ~21,600 user records, and an email table to justify in a data review.
- A real login-breaking hazard. `update_user` refuses to authenticate when
  someone's Mozilla-account email already sits on a different profile
  (`auth.py:208-216`). Import 37,000 addresses and that path can fire for
  *existing* SUMO users. Store none and it cannot.

The cost is real: 58% of migrated posts would show an author name with no
profile behind it. That is a product decision.

## Votes (kudos)

"Kudo" is Khoros's name; SUMO will call it a **vote**.

**Kudos attach to a single message, not to a thread.** Each kudo record carries
one `message.id`. On an idea the vote is a kudo on the topic message, which is
why Ideas keeps 95% of its kudos on topics while Discussions puts 78% on
replies. One kudo per person per message, always weight 1 — so a sum doubles as
a count.

A per-user vote table would be `(user_uid, message_uid, time)`. The Khoros
record also gives the voter's login, and **a real timestamp** — unlike tags or
status, votes are the one place Connect says *when* something happened.

Not exported yet. Costs are known: see
[What a full kudos export would cost](mozilla-connect.md#what-a-full-kudos-export-would-cost).
Roughly 30,553 requests and five hours for the whole community; the in-scope
subset is 26,589 messages.

!!! warning "Per-user votes expand the identity problem"

    Voters are not the same population as posters. Someone who only ever voted
    has no row in `message_authors` and is invisible in every figure above. We
    do not know how many such people exist, and we cannot know until the kudos
    detail is exported.

    So importing per-user votes means importing users we have not counted, of
    whom the same ~58% will have no matchable identity. If vote **totals** per
    message are enough, no voter identities are needed at all and the whole
    question disappears.

## Thread structure has nowhere to land

Connect threads are genuinely nested, to any depth. SUMO's are flat.

```python
# kitsune/forums/models.py:184
class Post(ModelBase):
    thread = models.ForeignKey("Thread", ...)       # a container, no parent

# kitsune/questions/models.py
class Answer(AAQBase):
    question = models.ForeignKey("Question", ...)   # same, flat list
```

Neither model has a parent or reply-to field, so `parent_message_uid` cannot be
represented as-is. Note this is the column that carries real information —
`topic_message_uid` is the redundant one, identical to `conversation_uid` in all
94,439 rows.

How much is at stake:

| | Messages |
|---|---:|
| Topics (`parent_message_uid` is null) | 19,429 |
| Replies to the thread opener — flattening is lossless | 63,423 |
| **Replies to another reply — structure would be lost** | **11,587** |
| Of those, deeper than depth 4 | 953 |

99% of messages sit at depth 4 or shallower, but the tail is long: the deepest
Discussions chain runs 22 levels. Ideas barely nests (deepest 8, only 14
messages past depth 4) because people vote and comment rather than argue in
branches. Discussions is where the real back-and-forth is, and it holds 794 of
those 953.

### Preferred approach: reuse the existing quote mechanism

SUMO already has a **Quote** button on forum posts and question answers
(`kitsune/forums/jinja2/forums/includes/post.html`,
`kitsune/questions/jinja2/questions/includes/answer.html`). It appends
wikimarkup built in `kitsune/sumo/static/sumo/js/questions.js:384`:

```
''<p>{user} [[#answer-123|said]]</p>''
<blockquote>{text}
</blockquote>
```

If the import emits the same markup whenever
`parent_message_uid != conversation_uid`, nested replies render exactly like a
user-written quote — **including a working anchor link back to the parent post**,
which makes the structure navigable rather than merely described. No schema
change, no template work, and nothing that looks imported.

The alternatives, for the record:

- **Flatten and accept the loss.** Cheapest. Fine for Ideas, lossy for
  Discussions.
- **Add a parent FK to `Post`.** Represents threading properly, but it is a
  migration plus view and template work, and SUMO's forums have never displayed
  nesting.

## Open questions

- **How many of the ~15,600 FxA UIDs already exist in `Profile.fxa_uid`?** The
  difference between linking existing users and creating most of them. Needs
  prod access.
- **Per-user votes, or totals only?** Decide before running the five-hour kudos
  export, because the answer changes who has to be imported.
- **`is_fxa_migrated` is only ever set by `create_user`.** Anything the import
  creates must set it deliberately, or those users are invisible to private
  messaging (`kitsune/messages/api.py:40`, `kitsune/users/api.py:71`).
- **Usernames.** Django requires one per user, and Connect logins may collide
  with existing SUMO usernames.
- **Confirm the quote-markup approach to threading**, and where Connect content
  lands — `forums.Post` and `questions.Answer` are both flat, so the choice of
  target does not change the problem. See
  [Thread structure has nowhere to land](#thread-structure-has-nowhere-to-land).
- **1,562 in-scope messages are authored by `Anonymous`** (`user_uid = -1`),
  Khoros's placeholder for deleted accounts. No person behind them, so they can
  never be matched.
- **Idea statuses have no SUMO equivalent.** Nine of them, including Delivered.
  See [Idea status](mozilla-connect.md#idea-status).
