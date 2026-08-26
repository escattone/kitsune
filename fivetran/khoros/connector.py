"""Fivetran connector for connect.mozilla.org (Khoros Communities).

Syncs the community's public content: every post with its body HTML, the people
who wrote them, the images they contain, and the reference tables describing the
forums themselves.

No credentials needed -- Khoros serves all public content to anonymous callers.
Set session_key in the configuration only to reach private boards.

API background and the traps that cost us the most time are in
docs/mozilla-connect.md. The standalone version is scripts/khoros_export.py.

Run locally with:

    cd fivetran/khoros
    uv run --no-project --with fivetran-connector-sdk --with requests fivetran debug
"""

import json
import re
import time

import requests
from fivetran_connector_sdk import Connector, FileUpload
from fivetran_connector_sdk import Logging as log
from fivetran_connector_sdk import Operations as op

DEFAULT_DOMAIN = "connect.mozilla.org"
DEFAULT_BOARDS = ["discussions", "ideas", "Labs", "community"]

# Khoros caps LIMIT at 1000, and -- this is the trap -- omitting LIMIT does not
# mean "everything". It quietly returns 25 rows plus a cursor, with nothing in
# the response saying it truncated. Always pass one.
LIQL_LIMIT = 1000

MAX_RETRIES = 8
# Deliberately slower than the one-off script's 0.25s. At that rate roughly one
# request in seven comes back 429, which is a fine trade when you are watching a
# script run once, and a poor one for a connector syncing unattended on a
# schedule. When the label and tag steps land -- 13,000 requests rather than 95
# -- this wants to become adaptive rather than a guess.
REQUEST_PAUSE = 0.5

IMG_SRC = re.compile(r"<img[^>]+src=[\"']([^\"']+)[\"']", re.IGNORECASE)
IMAGE_ID = re.compile(r"/image-id/([^/?\"']+)")

# Sizes to keep. medium suits inline display, large the detail view, and
# original is the only one we cannot regenerate later.
#
# Khoros builds these on demand and the URL can be written by hand from the
# image ID, so no API call is needed to discover them.
IMAGE_VARIANTS = ["original", "large", "medium"]
IMAGE_URL = "https://{domain}/t5/image/serverpage/image-id/{image_id}/image-size/{variant}"

IMAGE_EXTENSIONS = {
    "image/jpeg": ".jpg",
    "image/png": ".png",
    "image/gif": ".gif",
    "image/webp": ".webp",
    "image/bmp": ".bmp",
    "image/svg+xml": ".svg",
}

# Images come from a different service to the API -- Cloudflare-fronted, as the
# cf-cache-status header shows -- and it tolerates far more. 14,586 downloads at
# six concurrent workers, roughly 12 a second, drew no 429s at all. Serial at
# 0.1s is a fraction of that, so there is no reason to crawl the way the API
# makes us.
IMAGE_PAUSE = 0.1

# Verified as one working SELECT against the live API. Two traps: a single bad
# field rejects the whole query with "Invalid query syntax" and no hint which
# one, and `include_hidden_messages` is rejected even though SELECT * returns
# it. Test any addition on its own before adding it here.
MESSAGE_FIELDS = [
    "id",
    "subject",
    "body",
    "teaser",
    "search_snippet",
    "language",
    "message_type",
    "href",
    "view_href",
    "post_time",
    "last_publish_time",
    "depth",
    "is_image_comment",
    "parent.id",
    "topic.id",
    "conversation.id",
    "conversation.style",
    "conversation.messages_count",
    "conversation.solved",
    "conversation.last_post_time",
    "board.id",
    "author.id",
    "author.login",
    "author.view_href",
    "author.rank.id",
    "author.rank.name",
    "author.rank.position",
    "author.last_visit_time",
    "author.online_status",
    "author.deleted",
    "current_revision.id",
    "current_revision.revision_num",
    "current_revision.last_edit_time",
    "current_revision.last_edit_author.id",
    "current_revision.last_edit_author.login",
    "status.key",
    "status.name",
    "status.completed",
    "moderation_status",
    "is_solution",
    "can_accept_solution",
    "read_only",
    "edit_frozen",
    "is_promoted",
    "placeholder",
    "excluded_from_kudos_leaderboards",
    "popularity",
    "metrics.views",
    "kudos.sum(weight)",
    "replies.count(*)",
    "labels.count(*)",
    "tags.count(*)",
    "images.count(*)",
]

# Column types map from the BigQuery schema the standalone export produced:
# INTEGER -> LONG, TIMESTAMP -> UTC_DATETIME, FLOAT -> DOUBLE.
SCHEMA = [
    {
        "table": "messages",
        "primary_key": ["message_uid"],
        "columns": {
            "message_uid": "LONG",
            "conversation_uid": "LONG",
            "parent_message_uid": "LONG",
            "topic_message_uid": "LONG",
            "depth": "INT",
            "is_topic": "BOOLEAN",
            "is_image_comment": "BOOLEAN",
            "board_slug": "STRING",
            "message_type": "STRING",
            "href": "STRING",
            "view_href": "STRING",
            "subject": "STRING",
            "body_html": "STRING",
            "body_chars": "LONG",
            "teaser": "STRING",
            "search_snippet": "STRING",
            "language": "STRING",
            "post_time": "UTC_DATETIME",
            "last_publish_time": "UTC_DATETIME",
            "thread_style": "STRING",
            "thread_messages_count": "LONG",
            "thread_solved": "BOOLEAN",
            "thread_last_post_time": "UTC_DATETIME",
            "author_uid": "LONG",
            "author_login": "STRING",
            "revision_id": "STRING",
            "revision_num": "INT",
            "last_edit_time": "UTC_DATETIME",
            "last_edit_author_login": "STRING",
            "status_key": "STRING",
            "status_name": "STRING",
            "status_completed": "BOOLEAN",
            "moderation_status": "STRING",
            "is_solution": "BOOLEAN",
            "can_accept_solution": "BOOLEAN",
            "read_only": "BOOLEAN",
            "edit_frozen": "BOOLEAN",
            "is_promoted": "BOOLEAN",
            "placeholder": "BOOLEAN",
            "excluded_from_kudos_leaderboards": "BOOLEAN",
            "popularity": "DOUBLE",
            "views": "LONG",
            "kudos_weight": "LONG",
            "reply_count": "LONG",
            "label_count": "LONG",
            "tag_count": "LONG",
            "image_count": "LONG",
        },
    },
    {
        "table": "message_authors",
        "primary_key": ["user_uid"],
        "columns": {
            "user_uid": "LONG",
            "login": "STRING",
            "view_href": "STRING",
            "rank_id": "INT",
            "rank_name": "STRING",
            "rank_position": "INT",
            "last_visit_time": "UTC_DATETIME",
            "online_status": "STRING",
            "deleted": "BOOLEAN",
        },
    },
    {
        # Keyed on position, not image_id: images hot-linked from imgur and the
        # like have no image_id, and keying on it would collapse every external
        # image on a post into one null-keyed row.
        "table": "message_images",
        "primary_key": ["message_uid", "position"],
        "columns": {
            "message_uid": "LONG",
            "position": "INT",
            "image_id": "STRING",
            "url": "STRING",
            "source": "STRING",
        },
    },
    {
        # A label's identity is its text -- Khoros gives labels no separate ID,
        # unlike tags. So the text is half the primary key.
        "table": "message_labels",
        "primary_key": ["message_uid", "label"],
        "columns": {
            "message_uid": "LONG",
            "label": "STRING",
        },
    },
    {
        "table": "message_tags",
        "primary_key": ["message_uid", "tag_id"],
        "columns": {
            "message_uid": "LONG",
            "tag_id": "LONG",
            "tag": "STRING",
        },
    },
    {
        # One row per (image, size). Fivetran adds _fivetran_file_path holding
        # whatever we pass as FileUpload.path, which is why there is no gcs_uri
        # column here -- the platform records the destination itself.
        #
        # No byte count either. Khoros sends no Content-Length on image
        # responses, and since we stream straight through to Fivetran rather
        # than buffering, there is nothing to measure. The standalone script
        # reports sizes only because it downloads each file into memory first.
        "table": "image_files",
        "primary_key": ["image_id", "variant"],
        "columns": {
            "image_id": "STRING",
            "variant": "STRING",
            "filename": "STRING",
            "content_type": "STRING",
            "source_url": "STRING",
        },
    },
    {
        "table": "boards",
        "primary_key": ["id"],
        "columns": {
            "id": "STRING",
            "title": "STRING",
            "short_title": "STRING",
            "description": "STRING",
            "conversation_style": "STRING",
            "creation_date": "UTC_DATETIME",
            "views": "LONG",
            "position": "INT",
            "depth": "INT",
            "hidden": "BOOLEAN",
            "language": "STRING",
            "rating": "STRING",
            "allowed_labels": "STRING",
            "require_thread_root_label": "BOOLEAN",
            "comments_enabled": "BOOLEAN",
            "view_href": "STRING",
        },
    },
    {
        "table": "ranks",
        "primary_key": ["id"],
        "columns": {
            "id": "STRING",
            "name": "STRING",
            "position": "INT",
            "bold": "BOOLEAN",
            "color": "STRING",
            "rank_status": "STRING",
            "formula_enabled": "BOOLEAN",
        },
    },
]

REFERENCE_TABLES = ("boards", "ranks")

# How often the taxonomy pass checkpoints. A checkpoint both commits the rows
# buffered since the last one and saves how far we got, so a failure costs at
# most this many messages. Every message would mean 13,000 flushes; every 100
# keeps a failure cheap without the flushing becoming the bottleneck.
CHECKPOINT_EVERY = 100


class KhorosError(RuntimeError):
    pass


def backoff_seconds(response, attempt):
    """How long to wait before retrying, doubling each attempt.

    Khoros sends `Retry-After: 0` with its 429s. Taken literally that means
    retry immediately, which burns every attempt in milliseconds and fails the
    sync. Retry-After may lengthen the wait, never shorten it.
    """
    exponential = 2**attempt
    retry_after = response.headers.get("Retry-After")
    if retry_after:
        try:
            return min(max(float(retry_after), exponential), 300)
        except ValueError:
            pass
    return exponential


def query(session, domain, liql):
    """Run one LiQL query and return its data block.

    Khoros throttles readily -- on a long run roughly one request in seven comes
    back 429 -- but each has so far succeeded on the first retry a second later.
    So wait and try again rather than slowing the whole sync down.
    """
    url = f"https://{domain}/api/2.0/search"

    for attempt in range(MAX_RETRIES):
        try:
            response = session.get(url, params={"q": liql}, timeout=90)
        except requests.RequestException as exc:
            # Timeouts and dropped connections are as transient as a 429, and
            # just as survivable. Left unhandled they fail the whole sync --
            # which is what a read timeout on one slow page did on the first
            # real run of this connector.
            wait = 2**attempt
            log.warning(f"{type(exc).__name__} from Khoros, retrying in {wait}s")
            time.sleep(wait)
            continue

        if response.status_code == 429 or response.status_code >= 500:
            wait = backoff_seconds(response, attempt)
            log.warning(f"HTTP {response.status_code} from Khoros, retrying in {wait:g}s")
            time.sleep(wait)
            continue

        response.raise_for_status()
        payload = response.json()

        # Khoros returns HTTP 200 with an error body, so check the envelope too.
        if payload.get("status") != "success":
            raise KhorosError(f"{payload.get('message')} -- query was: {liql[:200]}")

        return payload.get("data", {})

    raise KhorosError(f"gave up after {MAX_RETRIES} attempts: {liql[:150]}")


def as_int(value):
    try:
        return int(value)
    except TypeError, ValueError:
        return None


def nested(item, *path):
    """Walk down nested dicts, e.g. nested(msg, 'author', 'rank', 'name')."""
    value = item
    for key in path:
        if not isinstance(value, dict):
            return None
        value = value.get(key)
    return value


def flatten_message(item):
    """Turn Khoros's nested JSON into flat columns.

    Names line up with the Fivetran event tables in mozilla_connect where they
    overlap (message_uid, conversation_uid) so the two can be joined.
    """
    body = item.get("body") or ""
    message_uid = as_int(item.get("id"))
    conversation_uid = as_int(nested(item, "conversation", "id"))

    return {
        "message_uid": message_uid,
        "conversation_uid": conversation_uid,
        "parent_message_uid": as_int(nested(item, "parent", "id")),
        "topic_message_uid": as_int(nested(item, "topic", "id")),
        "depth": item.get("depth"),
        "is_topic": item.get("depth") == 0 or message_uid == conversation_uid,
        "is_image_comment": item.get("is_image_comment"),
        "board_slug": nested(item, "board", "id"),
        "message_type": item.get("message_type"),
        "href": item.get("href"),
        "view_href": item.get("view_href"),
        "subject": item.get("subject"),
        "body_html": body,
        "body_chars": len(body),
        "teaser": item.get("teaser"),
        "search_snippet": item.get("search_snippet"),
        "language": item.get("language"),
        "post_time": item.get("post_time"),
        "last_publish_time": item.get("last_publish_time"),
        "thread_style": nested(item, "conversation", "style"),
        "thread_messages_count": nested(item, "conversation", "messages_count"),
        "thread_solved": nested(item, "conversation", "solved"),
        "thread_last_post_time": nested(item, "conversation", "last_post_time"),
        "author_uid": as_int(nested(item, "author", "id")),
        "author_login": nested(item, "author", "login"),
        "revision_id": nested(item, "current_revision", "id"),
        "revision_num": nested(item, "current_revision", "revision_num"),
        "last_edit_time": nested(item, "current_revision", "last_edit_time"),
        "last_edit_author_login": nested(item, "current_revision", "last_edit_author", "login"),
        # Ideas workflow state -- null on forum posts.
        "status_key": nested(item, "status", "key"),
        "status_name": nested(item, "status", "name"),
        "status_completed": nested(item, "status", "completed"),
        "moderation_status": item.get("moderation_status"),
        "is_solution": item.get("is_solution"),
        "can_accept_solution": item.get("can_accept_solution"),
        "read_only": item.get("read_only"),
        "edit_frozen": item.get("edit_frozen"),
        "is_promoted": item.get("is_promoted"),
        "placeholder": item.get("placeholder"),
        "excluded_from_kudos_leaderboards": item.get("excluded_from_kudos_leaderboards"),
        "popularity": item.get("popularity"),
        "views": nested(item, "metrics", "views"),
        "kudos_weight": as_int(nested(item, "kudos", "sum", "weight")),
        "reply_count": as_int(nested(item, "replies", "count")),
        "label_count": as_int(nested(item, "labels", "count")),
        "tag_count": as_int(nested(item, "tags", "count")),
        "image_count": as_int(nested(item, "images", "count")),
    }


def extract_author(item):
    """Pull an author record out of a message.

    The users collection returns zero rows to anonymous callers, so collecting
    authors as we sweep is the only way to get their details. Hence
    message_authors rather than users: it covers people who have posted, not
    everyone registered.

    No deduplication here -- Fivetran upserts by primary key, so the same author
    appearing on a thousand posts simply overwrites itself a thousand times.
    """
    uid = as_int(nested(item, "author", "id"))
    if uid is None:
        return None
    return {
        "user_uid": uid,
        "login": nested(item, "author", "login"),
        "view_href": nested(item, "author", "view_href"),
        "rank_id": as_int(nested(item, "author", "rank", "id")),
        "rank_name": nested(item, "author", "rank", "name"),
        "rank_position": nested(item, "author", "rank", "position"),
        "last_visit_time": nested(item, "author", "last_visit_time"),
        "online_status": nested(item, "author", "online_status"),
        "deleted": nested(item, "author", "deleted"),
    }


def extract_images(item):
    """Find images in a post by reading its body HTML.

    Free -- no extra request. Khoros inlines images as ordinary <img> tags, and
    about nine in ten posts with images have them inline. The bulk `images`
    collection is not a usable alternative: it truncates arbitrarily, and
    different sort orders return disjoint sets.
    """
    message_uid = as_int(item.get("id"))
    for position, url in enumerate(IMG_SRC.findall(item.get("body") or "")):
        found = IMAGE_ID.search(url)
        yield {
            "message_uid": message_uid,
            "position": position,
            "image_id": found.group(1) if found else None,
            "url": url,
            "source": "body_html",
        }


def sync_references(session, domain):
    """Boards and ranks. Tiny -- 4 and 36 rows -- so no paging needed.

    These are re-fetched in full on every sync, resume included, so truncating
    first is always safe and lets a deleted board or rank show up as deleted.
    """
    for table in REFERENCE_TABLES:
        columns = next(t["columns"] for t in SCHEMA if t["table"] == table)
        liql = f"SELECT {', '.join(columns)} FROM {table} LIMIT {LIQL_LIMIT}"

        op.truncate(table=table)
        items = query(session, domain, liql).get("items", [])
        for item in items:
            # Khoros stamps every object with its own "type"; we declare no such
            # column, and an undeclared key would be rejected.
            item.pop("type", None)
            op.upsert(table=table, data=item)

        log.info(f"{table}: {len(items)} rows")
        time.sleep(REQUEST_PAUSE)


def sync_board(session, domain, board, state, todo):
    """Walk one board by cursor, upserting as we go.

    Date filtering is not an option -- post_time is not a valid LiQL constraint
    -- so the cursor is the only way through. Stop only when the API stops
    handing one back: a short page is not the end.

    The sweep always runs from the first page, even on a resumed sync. It is
    only about 95 requests, and re-running it is what rebuilds `todo`: the list
    of messages carrying labels or tags. Skipping already-synced boards would
    leave that list incomplete and silently drop their taxonomy.

    Checkpointing each page is still worth it -- that is what commits the rows
    already upserted, so a failure late in the sweep doesn't discard the lot.
    """
    select_clause = ", ".join(MESSAGE_FIELDS)
    liql = (
        f"SELECT {select_clause} FROM messages "
        f"WHERE board.id = '{board}' ORDER BY post_time ASC LIMIT {LIQL_LIMIT}"
    )

    cursor, pages, rows = None, 0, 0

    while True:
        paged = f"{liql} CURSOR '{cursor}'" if cursor else liql
        data = query(session, domain, paged)
        items = data.get("items", [])
        cursor = data.get("next_cursor")
        pages += 1
        rows += len(items)

        for item in items:
            message = flatten_message(item)
            uid = message["message_uid"]
            op.upsert(table="messages", data=message)

            author = extract_author(item)
            if author:
                op.upsert(table="message_authors", data=author)

            inline_ids, next_position = set(), 0
            for image in extract_images(item):
                op.upsert(table="message_images", data=image)
                next_position = image["position"] + 1
                # Only Khoros-hosted images have an ID; externally linked ones
                # are not ours to re-host.
                if image["image_id"]:
                    todo["images"].add(image["image_id"])
                    inline_ids.add(image["image_id"])

            # The counts come free with the message, so we learn which posts are
            # worth a follow-up request without asking for anything extra.
            if message.get("label_count"):
                todo["labels"].append(uid)
            if message.get("tag_count"):
                todo["tags"].append(uid)

            # A post can carry images its body never mentions. image_count only
            # counts Khoros-hosted ones, so compare against the inline images
            # that have an ID -- an externally linked image would otherwise mask
            # a genuine shortfall.
            if (message.get("image_count") or 0) > len(inline_ids):
                todo["recover"][uid] = {"known": inline_ids, "next": next_position}

        op.checkpoint(state=state)
        log.info(f"{board} page {pages}: {len(items)} rows ({rows} total)")

        # Only a missing cursor means the end. A short page does not.
        if not cursor:
            return rows
        time.sleep(REQUEST_PAUSE)


def sync_taxonomy(session, domain, kind, uids, state):
    """Fetch labels or tags, one request per message that has any.

    This is the slow half of the sync -- roughly 13,000 requests and a couple of
    hours -- because Khoros offers no way to ask for many messages at once. IN()
    is rejected, and the labels and tags of a message cannot be selected inline
    when sweeping messages.

    Progress is checkpointed as the highest message ID finished. Khoros IDs
    climb over time, so a resumed sync skips anything at or below it, new posts
    sort to the end, and deleted ones simply drop out of the list.
    """
    table = f"message_{kind}"
    after_key = f"{kind}_after"
    after = state.get(after_key) or 0

    remaining = sorted(uid for uid in uids if uid > after)
    if not remaining:
        log.info(f"{kind}: nothing to do")
        return 0

    if after:
        log.info(f"{kind}: resuming after message {after}, {len(remaining)} to go")
    else:
        log.info(f"{kind}: {len(remaining)} messages to fetch")

    written = 0
    for index, uid in enumerate(remaining, 1):
        # LIMIT is not optional. Without one LiQL quietly returns 25 rows and a
        # cursor, so a message with 79 tags silently yields 25 of them.
        liql = f"SELECT * FROM {kind} WHERE messages.id = '{uid}' LIMIT {LIQL_LIMIT}"
        for item in query(session, domain, liql).get("items", []):
            if kind == "labels":
                row = {"message_uid": uid, "label": item.get("text")}
            else:
                row = {
                    "message_uid": uid,
                    "tag_id": as_int(item.get("id")),
                    "tag": item.get("text"),
                }
            op.upsert(table=table, data=row)
            written += 1

        state[after_key] = uid
        if index % CHECKPOINT_EVERY == 0:
            op.checkpoint(state=state)
            log.info(f"  {kind}: {index}/{len(remaining)} messages, {written} rows")
        time.sleep(REQUEST_PAUSE)

    op.checkpoint(state=state)
    log.info(f"{kind}: {written} rows from {len(remaining)} messages")
    return written


def recover_images(session, domain, recover, image_ids):
    """Find images attached to a post but never mentioned in its body HTML.

    Rare -- about 37 messages across the whole community -- and image_count
    tells us exactly which posts they are, so this costs one request each rather
    than one per message.

    Positions continue from where the body HTML left off. message_images is
    keyed on (message_uid, position), so restarting at zero would silently
    overwrite the inline images rather than add to them.
    """
    if not recover:
        return 0

    log.info(f"recovering images for {len(recover)} messages missing them from their body")
    found = 0

    for uid, info in recover.items():
        # LIMIT is not optional -- LiQL quietly returns 25 rows without one.
        liql = f"SELECT * FROM images WHERE messages.id = '{uid}' LIMIT {LIQL_LIMIT}"
        position = info["next"]
        for item in query(session, domain, liql).get("items", []):
            image_id = item.get("id")
            # The API returns every image on the message, including the ones the
            # body already gave us.
            if image_id in info["known"]:
                continue
            op.upsert(
                table="message_images",
                data={
                    "message_uid": uid,
                    "position": position,
                    "image_id": image_id,
                    "url": item.get("original_href"),
                    "source": "images_api",
                },
            )
            if image_id:
                image_ids.add(image_id)
            position += 1
            found += 1
        time.sleep(REQUEST_PAUSE)

    log.info(f"recovered {found} images the body HTML did not mention")
    return found


def sync_images(session, domain, image_ids, state):
    """Stream every image into the destination's file storage.

    Three sizes of each image, so about 14,500 files and 2 GB. The bytes are
    streamed straight from Khoros to Fivetran via response.raw -- nothing is
    buffered in memory or written to disk.

    Only Khoros-hosted images are here. Bodies also link images on imgur,
    githubusercontent and the like; those have no image_id, they aren't ours to
    re-host, and they're the ones most likely to be dead already.
    """
    after = state.get("images_after") or ""
    remaining = sorted(iid for iid in image_ids if iid > after)
    if not remaining:
        log.info("images: nothing to do")
        return 0

    log.info(f"images: {len(remaining)} images x {len(IMAGE_VARIANTS)} sizes")
    uploaded = 0

    for index, image_id in enumerate(remaining, 1):
        for variant in IMAGE_VARIANTS:
            url = IMAGE_URL.format(domain=domain, image_id=image_id, variant=variant)
            response = session.get(url, stream=True, timeout=90)
            response.raise_for_status()
            # Without this, a gzipped response would be handed over still encoded.
            response.raw.decode_content = True

            content_type = response.headers.get("Content-Type", "").split(";")[0].strip()
            filename = f"{image_id}{IMAGE_EXTENSIONS.get(content_type, '.bin')}"

            op.upsert(
                table="image_files",
                data={
                    "image_id": image_id,
                    "variant": variant,
                    "filename": filename,
                    "content_type": content_type,
                    "source_url": url,
                },
                # Mirrors the layout the standalone export used in the bucket.
                file=FileUpload(path=f"{variant}/{filename}", stream=response.raw),
            )
            uploaded += 1
            time.sleep(IMAGE_PAUSE)

        state["images_after"] = image_id
        if index % CHECKPOINT_EVERY == 0:
            op.checkpoint(state=state)
            log.info(f"  images: {index}/{len(remaining)} ({uploaded} files)")

    op.checkpoint(state=state)
    log.info(f"images: {uploaded} files from {len(remaining)} images")
    return uploaded


def schema(configuration: dict):
    """Declare the tables. Fivetran adds its own _fivetran_* columns."""
    return SCHEMA


def update(configuration: dict, state: dict):
    domain = configuration.get("domain", DEFAULT_DOMAIN)
    boards = [b.strip() for b in configuration.get("boards", "").split(",") if b.strip()]
    boards = boards or DEFAULT_BOARDS

    session = requests.Session()
    session.headers["Accept"] = "application/json"

    # Anonymous works for everything public on Connect, post bodies included.
    session_key = configuration.get("session_key")
    if session_key:
        session.headers["li-api-session-key"] = session_key

    # Propagate deletions with truncate(), which soft-deletes every existing row
    # by setting _fivetran_deleted = TRUE. The upserts that follow clear the flag
    # on everything still present, so whatever the source dropped stays marked.
    # It costs no extra API requests, and it is the only way to notice a
    # deletion -- a sweep of upserts can never say "this post is gone".
    #
    # Messages and the reference tables are re-fetched in full on every sync, so
    # truncating them is always safe. Labels and tags are not: a resumed sync
    # continues from where it stopped, so truncating would flag every row it has
    # already written as deleted with nothing to bring them back.
    for table in ("messages", "message_authors", "message_images"):
        op.truncate(table=table)
    for kind in ("labels", "tags"):
        if not state.get(f"{kind}_after"):
            op.truncate(table=f"message_{kind}")
    if not state.get("images_after"):
        op.truncate(table="image_files")

    sync_references(session, domain)

    todo = {"labels": [], "tags": [], "images": set(), "recover": {}}
    total = 0
    for board in boards:
        total += sync_board(session, domain, board, state, todo)

    log.info(
        f"{total} messages; {len(todo['labels'])} have labels, "
        f"{len(todo['tags'])} have tags "
        f"{len(todo['images'])} distinct images "
        f"({len(todo['labels']) + len(todo['tags'])} taxonomy requests and "
        f"{len(todo['images']) * len(IMAGE_VARIANTS)} file uploads to come)"
    )

    for kind in ("labels", "tags"):
        sync_taxonomy(session, domain, kind, todo[kind], state)

    # Before downloading, pick up any images the bodies never mentioned so their
    # IDs are included in the file sync.
    recover_images(session, domain, todo["recover"], todo["images"])
    sync_images(session, domain, todo["images"], state)

    log.info(f"sync complete: {total} messages across {len(boards)} boards")

    # Clear the state on success, so the next sync starts from scratch.
    #
    # State exists to resume an *interrupted* sync, not to skip work on the next
    # one. Leaving the taxonomy markers in place would mean later syncs saw
    # nothing left to fetch -- a connector that looks healthy while silently
    # going stale.
    #
    # Khoros has no way to ask "what changed since Tuesday" (post_time is not a
    # valid LiQL constraint), so a full re-sweep is the only way to see new and
    # edited posts. Re-upserting unchanged rows is harmless because Fivetran
    # matches on the primary key.
    op.checkpoint(state={})


connector = Connector(update=update, schema=schema)


if __name__ == "__main__":
    with open("configuration.json") as f:
        connector.debug(configuration=json.load(f))
