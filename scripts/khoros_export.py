#!/usr/bin/env python
"""Export connect.mozilla.org from the Khoros Community API v2.

A clean export straight from the API -- messages plus the related tables --
independent of the Fivetran event data in BigQuery.

No credentials needed. Everything public on Connect is served to anonymous
callers, post bodies included.

Tables it produces:
    messages          one row per post, 53 fields, including body HTML
    message_authors   everyone who posted, built up during the sweep
    message_images    images in each post, from the body HTML and the API
    message_labels    the curated labels on a message
    message_tags      the free-text tags on a message
    image_files       every downloaded image file and where it ended up
    boards / nodes    the four forums and the container tree
    ranks             the reputation ladder and staff badges

Run it through uv so it picks up the project's requests -- the system Python
doesn't have it. Everything writes to DEFAULT_OUT unless you pass --out. The
commands form a pipeline:

    uv run scripts/khoros_export.py fetch-references
    uv run scripts/khoros_export.py fetch-messages
    uv run scripts/khoros_export.py fetch-labels
    uv run scripts/khoros_export.py fetch-tags
    uv run scripts/khoros_export.py fetch-images
    uv run scripts/khoros_export.py load

Everything named fetch-* pulls data down from Khoros. `load` is the only one
that pushes anything up: tables into BigQuery, and image files into the bucket
named by GCS_BUCKET. Use --bucket to send them somewhere else, or --no-bucket
to load BigQuery and skip the images.

Every step is resumable, but resumable is not the same as incremental: a second
run skips whatever it already has rather than looking for changes. To pick up
new and edited posts, clear the old data first and let the pipeline redo it.

    uv run scripts/khoros_export.py clean          # messages and enrich
    uv run scripts/khoros_export.py clean --images # those plus the image files

Rough costs on a full run: the message sweep is about 95 requests and a few
minutes. fetch-labels and fetch-tags spend a request per message that carries
any, which the sweep counts up front so they can skip the rest -- together
around two and a half hours. fetch-images pulls three sizes of every
Khoros-hosted image, roughly 14,500 files and 2 GB.
"""

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from urllib.parse import quote

import requests

# Fivetran owns mozilla_connect and can drop and recreate its tables, so this
# export goes somewhere it won't be clobbered.
BQ_PROJECT = "moz-fx-sumo-prod"
BQ_DATASET = "mozilla_connect_content"

# Where the image files go. US-WEST1 like the other SUMO buckets, uniform
# access, public access prevention enforced. `load` uploads here by default;
# override with --bucket, or skip the upload entirely with --no-bucket.
GCS_BUCKET = "gs://sumo-prod-prod-connect-images"

# An absolute path, so the output lands in the same place wherever you run from.
# A relative default drops a couple of gigabytes into the repo if you happen to
# be sitting in it.
DEFAULT_OUT = Path.home() / "connect-export"

LIQL_LIMIT = 1000  # Khoros caps LIMIT at 1000.
REQUEST_PAUSE = 0.25
# 8 attempts of doubling waits adds up to about four minutes.
#
# Khoros throttles, and it isn't rare: on a long run at roughly 4 requests a
# second, about one request in seven comes back 429. Every one so far has
# succeeded on the first retry a second later, so the answer is to wait and try
# again, not to slow the whole job down. Short runs rarely see one at all, which
# suggests a rolling quota rather than a cap on instantaneous rate.
MAX_RETRIES = 8

DEFAULT_BOARDS = ["discussions", "ideas", "Labs", "community"]

# Verified as one working SELECT against the live API. Two traps here:
# `include_hidden_messages` is rejected even though SELECT * returns it, and a
# single bad field rejects the whole query with no clue which one. Test any
# addition on its own before adding it.
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

IMG_SRC = re.compile(r"<img[^>]+src=[\"']([^\"']+)[\"']", re.IGNORECASE)
IMAGE_ID = re.compile(r"/image-id/([^/?\"']+)")

# Sizes to keep. medium suits inline display, large the detail view, and
# original is the only one we can't regenerate later.
#
# Khoros builds these on demand and the URL can be written by hand from the
# image ID, so no API call is needed per image. The px hint in the API's own
# hrefs (999 for large, 400 for medium) can be left off.
IMAGE_VARIANTS = ["original", "large", "medium"]
IMAGE_URL = "https://{domain}/t5/image/serverpage/image-id/{image_id}/image-size/{variant}"

# Content types Khoros serves, mapped to a file extension.
IMAGE_EXTENSIONS = {
    "image/jpeg": ".jpg",
    "image/png": ".png",
    "image/gif": ".gif",
    "image/webp": ".webp",
    "image/bmp": ".bmp",
    "image/svg+xml": ".svg",
}
DOWNLOAD_WORKERS = 6


class KhorosError(RuntimeError):
    pass


def backoff_seconds(response, attempt):
    """How long to wait before retrying, doubling with each attempt.

    Khoros sends `Retry-After: 0` with its 429s, which taken at face value means
    "retry immediately" -- that just burns every attempt in milliseconds and
    fails. So Retry-After can lengthen the wait but never shorten it below what
    doubling would give.
    """
    exponential = 2**attempt
    retry_after = response.headers.get("Retry-After")
    if retry_after:
        try:
            # Usually a number of seconds, but may be an HTTP date we can't use.
            return min(max(float(retry_after), exponential), 300)
        except ValueError:
            pass
    return exponential


class KhorosClient:
    def __init__(self, domain, session_key=None):
        self.base = f"https://{domain}/api/2.0"
        self.session = requests.Session()
        self.session.headers["Accept"] = "application/json"
        # Anonymous is fine for public content; only send a key if we have one.
        if session_key:
            self.session.headers["li-api-session-key"] = session_key

    def query(self, liql):
        """Run one LiQL query and hand back the response's data block."""
        url = f"{self.base}/search?q={quote(liql)}"

        for attempt in range(MAX_RETRIES):
            response = self.session.get(url, timeout=90)

            # 429 = rate limited, 5xx = transient. Back off and retry.
            if response.status_code == 429 or response.status_code >= 500:
                wait = backoff_seconds(response, attempt)
                print(
                    f"  HTTP {response.status_code}, retrying in {wait:g}s",
                    file=sys.stderr,
                )
                time.sleep(wait)
                continue

            response.raise_for_status()
            payload = response.json()

            # Khoros returns HTTP 200 with an error body, so check the envelope too.
            if payload.get("status") != "success":
                raise KhorosError(f"{payload.get('message')} -- query was: {liql[:200]}")

            return payload.get("data", {})

        raise KhorosError(f"gave up after {MAX_RETRIES} attempts: {liql[:150]}")

    def page(self, liql, cursor=None):
        """Fetch one page, returning (items, next_cursor)."""
        if cursor:
            liql = f"{liql} CURSOR '{cursor}'"
        data = self.query(liql)
        return data.get("items", []), data.get("next_cursor")


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

    Names line up with the BigQuery event tables where they overlap
    (message_uid, conversation_uid) so the two can still be joined.
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
        # depth is the obvious signal, but the opening post also carries its own
        # ID as the conversation ID, which holds when depth comes back missing.
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

    The users collection returns zero rows to anonymous callers, so the only
    way to get author details is to collect them as we sweep. That is why this
    table is called message_authors and not users: it covers people who have
    posted, not everyone registered.
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

    This is free -- no extra request. Khoros inlines images as ordinary <img>
    tags, and about 9 in 10 posts with images have them inline. The bulk
    `images` collection is not a usable alternative: it truncates arbitrarily
    and different sort orders return disjoint sets.
    """
    message_uid = as_int(item.get("id"))
    rows = []
    for position, url in enumerate(IMG_SRC.findall(item.get("body") or "")):
        found = IMAGE_ID.search(url)
        rows.append(
            {
                "message_uid": message_uid,
                "position": position,
                "image_id": found.group(1) if found else None,
                "url": url,
                "source": "body_html",
            }
        )
    return rows


def write_ndjson(path, rows, mode="w"):
    path.parent.mkdir(parents=True, exist_ok=True)
    partial = path.with_suffix(path.suffix + ".partial")
    with partial.open(mode) as handle:
        for row in rows:
            handle.write(json.dumps(row) + "\n")
    partial.rename(path)
    return len(rows)


def read_ndjson(path):
    if not path.exists():
        return []
    with path.open() as handle:
        return [json.loads(line) for line in handle if line.strip()]


def cmd_fetch_references(client, args):
    """Export the small reference tables: boards, nodes, ranks.

    These describe the shape of the community rather than anything that
    happened in it, and between them they run to 46 rows.
    """
    out = Path(args.out) / "references"

    specs = [
        (
            "boards",
            "SELECT id, title, short_title, description, conversation_style, "
            "creation_date, views, position, depth, hidden, language, rating, "
            "allowed_labels, require_thread_root_label, comments_enabled, view_href "
            "FROM boards LIMIT 500",
        ),
        (
            "nodes",
            "SELECT id, title, short_title, description, node_type, depth, position, "
            "hidden, creation_date, views FROM nodes LIMIT 500",
        ),
        (
            "ranks",
            "SELECT id, name, position, bold, color, rank_status, formula_enabled "
            "FROM ranks LIMIT 500",
        ),
    ]

    for name, liql in specs:
        items = client.query(liql).get("items", [])
        for row in items:
            row.pop("type", None)
        write_ndjson(out / f"{name}.ndjson", items)
        print(f"{name}: {len(items)} rows")


def cmd_fetch_messages(client, args):
    """Sweep every board by cursor, writing one file per page."""
    root = Path(args.out)
    select_clause = ", ".join(MESSAGE_FIELDS)
    authors = {}
    grand_total = 0

    for board in args.board:
        board_dir = root / "messages" / board
        board_dir.mkdir(parents=True, exist_ok=True)
        state_path = board_dir / "_cursor.json"
        state = json.loads(state_path.read_text()) if state_path.exists() else {}
        # Authors are kept per board so a skipped board still contributes its
        # authors. The message rows only carry author_uid and login, not the rank
        # and visit fields, so they can't be rebuilt from the pages afterwards.
        board_authors_path = root / "derived" / f"message_authors_{board}.ndjson"
        board_authors = {a["user_uid"]: a for a in read_ndjson(board_authors_path)}

        if state.get("done"):
            print(f"{board}: already complete ({state.get('rows', 0)} rows)")
            grand_total += state.get("rows", 0)
            if not board_authors:
                # Authors can't be rebuilt from the message pages -- those only
                # carry author_uid and login, not rank or visit times.
                print(
                    f"  warning: no {board_authors_path.name}; delete "
                    f"{state_path} and re-run to recover this board's authors",
                    file=sys.stderr,
                )
            authors.update(board_authors)
            continue

        page_num = state.get("pages", 0)
        rows_total = state.get("rows", 0)
        cursor = state.get("cursor")
        liql = (
            f"SELECT {select_clause} FROM messages "
            f"WHERE board.id = '{board}' ORDER BY post_time ASC LIMIT {LIQL_LIMIT}"
        )

        if cursor:
            print(f"{board}: resuming from page {page_num + 1}")

        try:
            while True:
                items, cursor = client.page(liql, cursor)
                page_num += 1

                messages, images = [], []
                for item in items:
                    messages.append(flatten_message(item))
                    images.extend(extract_images(item))
                    author = extract_author(item)
                    if author:
                        board_authors[author["user_uid"]] = author

                write_ndjson(board_dir / f"page_{page_num:05d}.ndjson", messages)
                if images:
                    write_ndjson(board_dir / f"images_{page_num:05d}.ndjson", images)
                # Rewrite alongside the checkpoint so a resume never loses authors.
                write_ndjson(board_authors_path, list(board_authors.values()))
                rows_total += len(messages)

                # Checkpoint after the page lands, so a resume never skips one.
                state_path.write_text(
                    json.dumps(
                        {
                            "cursor": cursor,
                            "pages": page_num,
                            "rows": rows_total,
                            "done": not cursor,
                        }
                    )
                )
                print(f"  {board} page {page_num:>3}: {len(items):>5} rows ({rows_total} total)")

                # Only a missing cursor means the end. A short page does not.
                if not cursor:
                    break
                time.sleep(REQUEST_PAUSE)

        except (KhorosError, requests.RequestException) as exc:
            print(f"FAILED on {board} page {page_num + 1}: {exc}", file=sys.stderr)
            print("  rerun to resume from the last checkpoint", file=sys.stderr)
            raise SystemExit(1)

        authors.update(board_authors)
        grand_total += rows_total
        print(f"{board}: {rows_total} messages")

    # Merge every per-board author file on disk, not just this run's boards, so
    # fetching one board at a time still yields a complete authors table.
    for path in sorted((root / "derived").glob("message_authors_*.ndjson")):
        for author in read_ndjson(path):
            authors.setdefault(author["user_uid"], author)
    write_ndjson(root / "derived" / "message_authors.ndjson", list(authors.values()))
    print(f"\n{grand_total} messages, {len(authors)} distinct authors -> {root}")
    print("note: count(*) reports ~782 more messages than a sweep can reach")
    print("      (depth-less rows, almost certainly image comments)")


def read_ndjson_dir(directory):
    rows = []
    for path in sorted(directory.glob("page_*.ndjson")):
        rows.extend(read_ndjson(path))
    return rows


def khoros_inline_ids(board_dir):
    """Map each message to the Khoros image IDs already found in its body.

    Only images with an image_id count. A body can also link images hosted
    elsewhere -- imgur, github and so on -- and those aren't in the images
    collection, so counting them would hide a genuine shortfall.

    Returning the IDs rather than a tally also lets the API pass skip images it
    has already seen, instead of recording them twice.
    """
    seen = {}
    for path in sorted(board_dir.glob("images_*.ndjson")):
        for row in read_ndjson(path):
            if row.get("image_id"):
                seen.setdefault(row["message_uid"], set()).add(row["image_id"])
    return seen


def all_messages(root):
    """Every message row the sweep wrote, across all boards."""
    messages_dir = root / "messages"
    if not messages_dir.exists():
        raise SystemExit("run `fetch-messages` first")
    for board_dir in sorted(messages_dir.iterdir()):
        if board_dir.is_dir():
            yield from read_ndjson_dir(board_dir)


def fetch_per_message(client, root, count_field, collection, build_row, out_name, batch=100):
    """Query one collection per message, for messages that have any.

    The sweep counted labels, tags and images on every message, so we can skip
    the ones with none -- usually the overwhelming majority. Drive off those
    counts rather than off is_topic: labels only appear on thread openers, but
    replies carry tags of their own, and filtering to topics silently drops them.

    Results are saved every `batch` messages alongside the list of messages
    already done, so an interrupted run resumes instead of starting over. These
    runs take an hour or more; losing all of it to one failure at the end is not
    an acceptable way to spend an afternoon.
    """
    out_path = root / "enrich" / out_name
    state_path = root / "enrich" / f"_{out_path.stem}_done.json"

    done = set(json.loads(state_path.read_text())) if state_path.exists() else set()
    rows = read_ndjson(out_path) if done else []

    todo = [r["message_uid"] for r in all_messages(root) if r.get(count_field)]
    remaining = [uid for uid in todo if uid not in done]

    print(f"{len(todo)} messages have {collection}", end="")
    print(f", {len(done)} already fetched, {len(remaining)} to go" if done else "")

    def save():
        write_ndjson(out_path, rows)
        state_path.write_text(json.dumps(sorted(done)))

    for index, uid in enumerate(remaining, 1):
        # LIMIT is not optional. Without one LiQL quietly returns 25 rows and a
        # cursor, so a message with 79 tags silently yields 25 of them.
        data = client.query(
            f"SELECT * FROM {collection} WHERE messages.id = '{uid}' LIMIT {LIQL_LIMIT}"
        )
        rows.extend(build_row(uid, item) for item in data.get("items", []))
        done.add(uid)
        if index % batch == 0:
            save()
            print(f"  {index}/{len(remaining)} messages ({len(rows)} {collection})")
        time.sleep(REQUEST_PAUSE)

    save()
    print(f"{len(rows)} {collection} written")


def cmd_fetch_labels(client, args):
    """Fetch the curated labels on each message that carries any."""
    fetch_per_message(
        client,
        Path(args.out),
        "label_count",
        "labels",
        lambda uid, item: {"message_uid": uid, "label": item.get("text")},
        "message_labels.ndjson",
    )


def cmd_fetch_tags(client, args):
    """Fetch the free-text tags on each message that carries any."""
    fetch_per_message(
        client,
        Path(args.out),
        "tag_count",
        "tags",
        lambda uid, item: {
            "message_uid": uid,
            "tag_id": as_int(item.get("id")),
            "tag": item.get("text"),
        },
        "message_tags.ndjson",
    )


def recover_missing_images(client, root):
    """Find images attached to a post but never mentioned in its body HTML.

    Rare -- about 37 messages community-wide -- and image_count tells us exactly
    which ones, so this costs a request each rather than one per message.
    """
    need = []
    messages_dir = root / "messages"
    for board_dir in sorted(messages_dir.iterdir()):
        if not board_dir.is_dir():
            continue
        inline = khoros_inline_ids(board_dir)
        for row in read_ndjson_dir(board_dir):
            already = inline.get(row["message_uid"], set())
            if (row.get("image_count") or 0) > len(already):
                need.append((row["message_uid"], already))

    print(f"{len(need)} messages have images missing from their body HTML")
    rows = []
    for index, (uid, already) in enumerate(need, 1):
        # LIMIT is not optional -- LiQL defaults to 25 rows without one.
        data = client.query(f"SELECT * FROM images WHERE messages.id = '{uid}' LIMIT {LIQL_LIMIT}")
        position = 0
        for item in data.get("items", []):
            # The API returns every image on the message, including any the body
            # already gave us. Keep only the ones we're missing.
            if item.get("id") in already:
                continue
            rows.append(
                {
                    "message_uid": uid,
                    "position": position,
                    "image_id": item.get("id"),
                    "url": item.get("original_href"),
                    "source": "images_api",
                }
            )
            position += 1
        if index % 100 == 0:
            print(f"  {index}/{len(need)} messages ({len(rows)} images)")
        time.sleep(REQUEST_PAUSE)

    write_ndjson(root / "enrich" / "message_images_api.ndjson", rows)
    print(f"{len(rows)} images recovered from the API")


def collect_image_ids(root):
    """Every Khoros-hosted image in the export, deduplicated.

    Images hosted elsewhere (imgur, github and so on) come through with no
    image_id and are skipped -- they aren't our content to re-host, and they're
    the ones most likely to be dead links already.
    """
    seen = {}
    sources = list((root / "messages").glob("*/images_*.ndjson"))
    recovered = root / "enrich" / "message_images_api.ndjson"
    if recovered.exists():
        sources.append(recovered)

    for path in sources:
        for row in read_ndjson(path):
            if row.get("image_id"):
                seen.setdefault(row["image_id"], row["message_uid"])
    return seen


def download_image(session, domain, image_id, variant, out_dir):
    """Fetch one variant. Returns a row, or None if it was already on disk."""
    url = IMAGE_URL.format(domain=domain, image_id=image_id, variant=variant)

    # Resume: anything already downloaded is left alone.
    existing = list(out_dir.glob(f"{image_id}.*"))
    if existing and existing[0].stat().st_size > 0:
        return None

    for attempt in range(MAX_RETRIES):
        response = session.get(url, timeout=90)
        if response.status_code == 429 or response.status_code >= 500:
            wait = backoff_seconds(response, attempt)
            print(
                f"  HTTP {response.status_code} on {image_id}, waiting {wait:g}s", file=sys.stderr
            )
            time.sleep(wait)
            continue
        response.raise_for_status()

        content_type = response.headers.get("Content-Type", "").split(";")[0].strip()
        target = out_dir / f"{image_id}{IMAGE_EXTENSIONS.get(content_type, '.bin')}"
        target.write_bytes(response.content)
        return {
            "image_id": image_id,
            "variant": variant,
            "filename": target.name,
            "bytes": len(response.content),
            "content_type": content_type,
            "source_url": url,
        }

    raise KhorosError(f"could not download {image_id} ({variant})")


def cmd_fetch_images(client, args):
    """Download image files so they can be re-hosted somewhere we control."""
    root = Path(args.out)
    domain = os.environ.get("KHOROS_DOMAIN", "connect.mozilla.org")

    # Catch the stragglers first, so their IDs are included in the download.
    recover_missing_images(client, root)

    images = collect_image_ids(root)
    if not images:
        raise SystemExit("no images found -- run `fetch-messages` first")

    jobs = [(iid, v) for iid in sorted(images) for v in IMAGE_VARIANTS]
    print(f"{len(images)} images x {len(IMAGE_VARIANTS)} variants = {len(jobs)} downloads")

    for variant in IMAGE_VARIANTS:
        (root / "images" / variant).mkdir(parents=True, exist_ok=True)

    # One session per worker thread; requests sessions aren't meant to be shared.
    local = threading.local()

    def fetch(job):
        image_id, variant = job
        if not hasattr(local, "session"):
            local.session = requests.Session()
        return download_image(local.session, domain, image_id, variant, root / "images" / variant)

    rows, skipped, done = [], 0, 0
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        for result in pool.map(fetch, jobs):
            done += 1
            if result is None:
                skipped += 1
            else:
                rows.append(result)
            if done % 500 == 0:
                print(f"  {done}/{len(jobs)} ({len(rows)} fetched, {skipped} already present)")

    # Existing rows are kept so a resumed run doesn't lose earlier records.
    # gcs_uri is filled in later by `load`, which is where the bucket is known.
    manifest = root / "derived" / "image_files.ndjson"
    by_key = {(r["image_id"], r["variant"]): r for r in read_ndjson(manifest)}
    for row in rows:
        by_key[(row["image_id"], row["variant"])] = row

    write_ndjson(manifest, list(by_key.values()))
    total = sum(r["bytes"] for r in by_key.values())
    print(f"\n{len(rows)} downloaded, {skipped} already present")
    print(f"{len(by_key)} files, {total / 1024 / 1024:.0f} MB in {root / 'images'}")


SCHEMAS = {
    "messages": [
        ("message_uid", "INTEGER", "REQUIRED"),
        ("conversation_uid", "INTEGER"),
        ("parent_message_uid", "INTEGER"),
        ("topic_message_uid", "INTEGER"),
        ("depth", "INTEGER"),
        ("is_topic", "BOOLEAN"),
        ("is_image_comment", "BOOLEAN"),
        ("board_slug", "STRING"),
        ("message_type", "STRING"),
        ("href", "STRING"),
        ("view_href", "STRING"),
        ("subject", "STRING"),
        ("body_html", "STRING"),
        ("body_chars", "INTEGER"),
        ("teaser", "STRING"),
        ("search_snippet", "STRING"),
        ("language", "STRING"),
        ("post_time", "TIMESTAMP"),
        ("last_publish_time", "TIMESTAMP"),
        ("thread_style", "STRING"),
        ("thread_messages_count", "INTEGER"),
        ("thread_solved", "BOOLEAN"),
        ("thread_last_post_time", "TIMESTAMP"),
        ("author_uid", "INTEGER"),
        ("author_login", "STRING"),
        ("revision_id", "STRING"),
        ("revision_num", "INTEGER"),
        ("last_edit_time", "TIMESTAMP"),
        ("last_edit_author_login", "STRING"),
        ("status_key", "STRING"),
        ("status_name", "STRING"),
        ("status_completed", "BOOLEAN"),
        ("moderation_status", "STRING"),
        ("is_solution", "BOOLEAN"),
        ("can_accept_solution", "BOOLEAN"),
        ("read_only", "BOOLEAN"),
        ("edit_frozen", "BOOLEAN"),
        ("is_promoted", "BOOLEAN"),
        ("placeholder", "BOOLEAN"),
        ("excluded_from_kudos_leaderboards", "BOOLEAN"),
        ("popularity", "FLOAT"),
        ("views", "INTEGER"),
        ("kudos_weight", "INTEGER"),
        ("reply_count", "INTEGER"),
        ("label_count", "INTEGER"),
        ("tag_count", "INTEGER"),
        ("image_count", "INTEGER"),
    ],
    "message_images": [
        ("message_uid", "INTEGER", "REQUIRED"),
        ("position", "INTEGER"),
        ("image_id", "STRING"),
        ("url", "STRING"),
        ("source", "STRING"),
    ],
    "message_authors": [
        ("user_uid", "INTEGER", "REQUIRED"),
        ("login", "STRING"),
        ("view_href", "STRING"),
        ("rank_id", "INTEGER"),
        ("rank_name", "STRING"),
        ("rank_position", "INTEGER"),
        ("last_visit_time", "TIMESTAMP"),
        ("online_status", "STRING"),
        ("deleted", "BOOLEAN"),
    ],
    "message_labels": [
        ("message_uid", "INTEGER", "REQUIRED"),
        ("label", "STRING"),
    ],
    "message_tags": [
        ("message_uid", "INTEGER", "REQUIRED"),
        ("tag_id", "INTEGER"),
        ("tag", "STRING"),
    ],
    "image_files": [
        ("image_id", "STRING", "REQUIRED"),
        ("variant", "STRING"),
        ("filename", "STRING"),
        ("bytes", "INTEGER"),
        ("content_type", "STRING"),
        ("source_url", "STRING"),
        ("gcs_uri", "STRING"),
    ],
}


def directory_size(path):
    return sum(f.stat().st_size for f in path.rglob("*") if f.is_file())


def cmd_clean(_client, args):
    """Delete parts of the export so the next run fetches them again.

    Messages and the label/tag data always go together. The enrich state records
    message IDs, so keeping it against a re-swept messages directory would leave
    it pointing at rows that may no longer exist.

    Images are kept unless you ask otherwise. They take half an hour to pull, and
    fetch-images skips anything already on disk, so hanging on to them makes a
    refresh much cheaper.
    """
    root = Path(args.out)
    if not root.exists():
        raise SystemExit(f"{root} does not exist")

    if args.everything:
        targets = [root]
    else:
        targets = [root / "messages", root / "enrich"]
        if args.images:
            targets.append(root / "images")

    targets = [t for t in targets if t.exists()]
    if not targets:
        print("nothing to clean")
        return

    print("about to delete:")
    for target in targets:
        print(f"  {target}  ({directory_size(target) / 1024 / 1024:.0f} MB)")

    if not args.yes:
        if input("type 'yes' to continue: ").strip().lower() != "yes":
            raise SystemExit("cancelled")

    for target in targets:
        shutil.rmtree(target)
        print(f"removed {target}")

    # The manifest describes files we just deleted, so it has to go too.
    if args.images or args.everything:
        manifest = root / "derived" / "image_files.ndjson"
        manifest.unlink(missing_ok=True)


def ensure_dataset():
    """Create the dataset if it isn't there. Harmless when it already exists."""
    subprocess.run(
        ["bq", f"--project_id={BQ_PROJECT}", "mk", "--force", "--dataset", BQ_DATASET],
        check=True,
    )


def concat(files, target):
    """Join the page files into one, because bq load takes a single local file.

    No wildcards, no comma-separated lists -- that only works for files already
    in Cloud Storage. The whole messages export is around 180 MB, well within
    what one upload handles.
    """
    target.parent.mkdir(parents=True, exist_ok=True)
    rows = 0
    with target.open("w") as out:
        for path in files:
            with path.open() as handle:
                for line in handle:
                    if line.strip():
                        out.write(line)
                        rows += 1
    return rows


def bq_load(table, files, out_dir, extra_flags=()):
    """Load NDJSON into BigQuery with an explicit schema.

    Not --autodetect: it guesses from the first rows and mistypes any column
    whose early values happen to be null.
    """
    if not files:
        print(f"{table}: nothing to load")
        return

    schema = [
        {"name": n, "type": t, "mode": (m[0] if m else "NULLABLE")} for n, t, *m in SCHEMAS[table]
    ]
    schema_path = out_dir / "_load" / f"schema_{table}.json"
    schema_path.parent.mkdir(parents=True, exist_ok=True)
    schema_path.write_text(json.dumps(schema, indent=2))

    combined = out_dir / "_load" / f"{table}.ndjson"
    rows = concat(files, combined)

    subprocess.run(
        [
            "bq",
            f"--project_id={BQ_PROJECT}",
            "load",
            "--source_format=NEWLINE_DELIMITED_JSON",
            "--replace",
            f"--schema={schema_path}",
            *extra_flags,
            f"{BQ_DATASET}.{table}",
            str(combined),
        ],
        check=True,
    )
    print(f"{table}: loaded {rows} rows from {len(files)} file(s)")


def upload_images(root, bucket):
    """Copy the image files into a bucket and record where each one landed.

    The bucket has to exist already -- see check_bucket for why we don't make it.
    """
    local = root / "images"
    if not local.exists():
        print("no image files to upload; run fetch-images first")
        return

    check_bucket(bucket)
    print(f"uploading {local} to {bucket}")
    subprocess.run(
        ["gcloud", "storage", "rsync", "--recursive", str(local), bucket],
        check=True,
    )

    # Stamp the destination onto the manifest before it goes into BigQuery.
    manifest = root / "derived" / "image_files.ndjson"
    rows = read_ndjson(manifest)
    prefix = bucket.rstrip("/")
    for row in rows:
        row["gcs_uri"] = f"{prefix}/{row['variant']}/{row['filename']}"
    write_ndjson(manifest, rows)
    print(f"uploaded {len(rows)} files")


def check_bucket(bucket):
    """Fail early with a usable message if the bucket isn't there.

    Deliberately not created for you. Unlike a BigQuery dataset, a bucket name is
    unique across the whole of Google Cloud and carries choices -- location,
    storage class, public access, retention -- that usually belong to whoever
    owns the infrastructure, not to an export script.
    """
    name = bucket.split("/")[2] if bucket.startswith("gs://") else bucket
    result = subprocess.run(
        ["gcloud", "storage", "buckets", "describe", f"gs://{name}", "--format=value(name)"],
        capture_output=True,
        text=True,
        # We want the return code, not an exception -- a failure here means the
        # bucket is missing, which we report with instructions rather than raise.
        check=False,
    )
    if result.returncode != 0:
        raise SystemExit(
            f"bucket gs://{name} not found (or no access).\n"
            f"Create it with something like:\n"
            f"  gcloud storage buckets create gs://{name} \\\n"
            f"      --project={BQ_PROJECT} --location=US \\\n"
            f"      --uniform-bucket-level-access"
        )


def cmd_load(_client, args):
    root = Path(args.out)
    messages_dir = root / "messages"
    ensure_dataset()
    if args.bucket and not args.no_bucket:
        upload_images(root, args.bucket)

    message_files, image_files = [], []
    if messages_dir.exists():
        for board_dir in sorted(messages_dir.iterdir()):
            if board_dir.is_dir():
                message_files.extend(sorted(board_dir.glob("page_*.ndjson")))
                image_files.extend(sorted(board_dir.glob("images_*.ndjson")))

    bq_load(
        "messages",
        message_files,
        root,
        (
            # Clustering on the join keys keeps thread queries cheap.
            "--clustering_fields=conversation_uid,message_uid",
            "--time_partitioning_field=post_time",
            "--time_partitioning_type=MONTH",
        ),
    )
    # Images come from two places: the body HTML during the sweep, and the API
    # for the handful of posts whose body didn't mention them.
    recovered = root / "enrich" / "message_images_api.ndjson"
    if recovered.exists():
        image_files.append(recovered)
    bq_load("message_images", image_files, root)
    author_file = root / "derived" / "message_authors.ndjson"
    bq_load("message_authors", [author_file] if author_file.exists() else [], root)
    manifest = root / "derived" / "image_files.ndjson"
    bq_load("image_files", [manifest] if manifest.exists() else [], root)
    for table in ("message_labels", "message_tags"):
        path = root / "enrich" / f"{table}.ndjson"
        bq_load(table, [path] if path.exists() else [], root)

    # The reference tables are tiny; autodetect is fine and saves three schemas.
    for name in ("boards", "nodes", "ranks"):
        path = root / "references" / f"{name}.ndjson"
        if path.exists():
            subprocess.run(
                [
                    "bq",
                    f"--project_id={BQ_PROJECT}",
                    "load",
                    "--source_format=NEWLINE_DELIMITED_JSON",
                    "--replace",
                    "--autodetect",
                    f"{BQ_DATASET}.{name}",
                    str(path),
                ],
                check=True,
            )
            print(f"{name}: loaded")


def get_session_key(domain):
    """Return a session key, or None to call the API anonymously.

    Anonymous covers everything public, which is all this export needs. This
    only matters if you add credentials to reach private boards or full user
    profiles.
    """
    key = os.environ.get("KHOROS_SESSION_KEY")
    if key:
        return key

    user = os.environ.get("KHOROS_USER")
    password = os.environ.get("KHOROS_PASSWORD")
    if not (user and password):
        return None

    response = requests.post(
        f"https://{domain}/restapi/vc/authentication/sessions/login",
        params={
            "user.login": user,
            "user.password": password,
            "restapi.response_format": "json",
        },
        timeout=30,
    )
    response.raise_for_status()
    body = response.json()

    try:
        return body["response"]["value"]["$"]
    except KeyError, TypeError:
        raise SystemExit(f"could not read a session key from the login response: {body}")


def main():
    # --out lives on a shared parent so it works after the subcommand, which is
    # where anyone would naturally type it.
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument(
        "--out",
        default=str(DEFAULT_OUT),
        help=f"output directory (default: {DEFAULT_OUT})",
    )

    parser = argparse.ArgumentParser(
        description=__doc__,
        # Keep the docstring's own line breaks instead of reflowing it.
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser("fetch-references", parents=[common], help="boards, nodes and ranks")

    messages = subparsers.add_parser(
        "fetch-messages", parents=[common], help="sweep every board for messages"
    )
    messages.add_argument(
        "--board",
        action="append",
        help=f"board slug, repeatable (default: {', '.join(DEFAULT_BOARDS)})",
    )

    subparsers.add_parser("fetch-labels", parents=[common], help="labels per message (slow)")
    subparsers.add_parser("fetch-tags", parents=[common], help="tags per message (slow)")

    images = subparsers.add_parser(
        "fetch-images", parents=[common], help="download the image files"
    )
    images.add_argument("--workers", type=int, default=DOWNLOAD_WORKERS)

    load = subparsers.add_parser(
        "load", parents=[common], help="upload to BigQuery and, with --bucket, to GCS"
    )
    load.add_argument(
        "--bucket",
        default=GCS_BUCKET,
        help=f"gs://bucket/prefix for the image files, must already exist (default: {GCS_BUCKET})",
    )
    load.add_argument(
        "--no-bucket",
        action="store_true",
        help="load BigQuery only, skipping the image upload",
    )

    clean = subparsers.add_parser(
        "clean", parents=[common], help="delete parts of the export so they refetch"
    )
    clean.add_argument(
        "--images", action="store_true", help="also delete the downloaded image files"
    )
    clean.add_argument(
        "--everything", action="store_true", help="delete the whole output directory"
    )
    clean.add_argument("--yes", action="store_true", help="skip the confirmation prompt")

    args = parser.parse_args()
    if getattr(args, "board", None) is None:
        args.board = DEFAULT_BOARDS
    domain = os.environ.get("KHOROS_DOMAIN", "connect.mozilla.org")

    handlers = {
        "fetch-references": cmd_fetch_references,
        "fetch-messages": cmd_fetch_messages,
        "fetch-labels": cmd_fetch_labels,
        "fetch-tags": cmd_fetch_tags,
        "fetch-images": cmd_fetch_images,
        "load": cmd_load,
        "clean": cmd_clean,
    }
    # `load` only talks to Google and `clean` only touches local files.
    offline = {"load", "clean"}
    client = None if args.command in offline else KhorosClient(domain, get_session_key(domain))

    handlers[args.command](client, args)


if __name__ == "__main__":
    main()
