# Run this with ./manage.py runscript make_pgloader_script [--script-args <num-prefetch-rows>]

import os
import stat
from pathlib import Path


# We'll transfer tables with more than 1k rows one at a time.
TABLES_SINGLES = [
    "wiki_helpfulvotemetadata",
    "wiki_helpfulvote",
    "journal_record",
    "dashboards_wikimetric",
    "questions_votemetadata",
    "questions_questionvote",
    "auth_user",
    "users_profile",
    "questions_questionmetadata",
    "users_accountevent",
    "taggit_taggeditem",
    "tidings_watch",
    "questions_answervote",
    "users_setting",
    "actstream_follow",
    "questions_answer",
    "notifications_notification",
    "actstream_action",
    "questions_questionvisits",
    "questions_question",
    "auth_user_groups",
    "wiki_revision",
    "upload_imageattachment",
    "wiki_documentlink",
    "wiki_documentimage",
    "wiki_document_contributors",
    "messages_outboxmessage_to",
    "messages_inboxmessage",
    "messages_outboxmessage",
    "wiki_document",
    "users_deactivation",
    "kpi_metric",
    "forums_post",
    "users_profile_products",
    "gallery_image",
    "kpi_retentionmetric",
    "flagit_flaggedobject",
    "kbforums_post",
    "django_admin_log",
    "dashboards_wikidocumentvisits",
    "forums_thread",
    "tidings_watchfilter",
    "kbforums_thread",
    "taggit_tag",
    "notifications_realtimeregistration",
    "wiki_document_topics",
    "wiki_document_products",
    "wiki_draftrevision",
    "notifications_pushnotificationregistration",
    "badger_award",
    "kpi_cohort",
    "users_registrationprofile",
]

# We'll transfer this group of tables, each with less than 1k rows, all at the same time.
TABLES_SKINNY = [
    "auth_user_user_permissions",
    "users_emailchange",
    "auth_group_permissions",
    "inproduct_redirect",
    "auth_permission",
    "wiki_document_related_documents",
    "wiki_locale_editors",
    "products_topic",
    "products_version",
    "wiki_locale_reviewers",
    "gallery_video",
    "groups_groupprofile_leaders",
    "announcements_announcement",
    "wiki_locale_leaders",
    "django_content_type",
    "auth_group",
    "products_product_platforms",
    "product_details_productdetailsfile",
    "groups_groupprofile",
    "announcements_announcement_groups",
    "wiki_locale",
    "badger_badge",
    "questions_questionlocale_products",
    "products_product",
    "karma_title_users",
    "forums_forum",
    "kpi_metrickind",
    "authtoken_token",
    "wiki_importantdate",
    "questions_questionlocale",
    "products_platform",
    "waffle_flag",
    "waffle_switch",
    "karma_title",
    "kpi_cohortkind",
    "waffle_flag_users",
    "karma_title_groups",
    "waffle_sample",
    "questions_aaqconfig",
    "guardian_userobjectpermission",
    "waffle_flag_groups",
    "postcrash_signature",
    "django_site",
    "guardian_groupobjectpermission",
    "questions_aaqconfig_pinned_articles",
]

CONFIG_TEMPLATE = """
LOAD DATABASE
    FROM      mysql://sumo:{}@172.16.16.242/support_mozilla_com
    INTO postgresql://sumo:{}@172.16.16.239/sumo_prod

    INCLUDING ONLY TABLE NAMES MATCHING {}

    WITH no truncate,
         data only,
         create no tables,
         include no drop,
         create no indexes,
         no foreign keys,
         preserve index names,
         reset sequences,
         workers = 4,
         concurrency = 1,
         batch rows = {},
         prefetch rows = {}

    SET PostgreSQL PARAMETERS
        session_replication_role = 'replica'

    CAST COLUMN actstream_action.data TO jsonb,
         COLUMN tidings_watch.secret TO varchar keep typemod using remove-null-characters,
         COLUMN tidings_watchfilter.value TO bigint drop typemod,
         COLUMN django_admin_log.action_flag TO smallint drop typemod,
         COLUMN users_accountevent.status TO smallint drop typemod,
         type char TO char keep typemod using remove-null-characters,
         type varchar TO varchar keep typemod using remove-null-characters,
         type tinytext TO text using remove-null-characters,
         type text TO text using remove-null-characters,
         type mediumtext TO text using remove-null-characters,
         type longtext TO text using remove-null-characters,
         type int with extra auto_increment to integer,
         type integer with extra auto_increment to integer,
         type int when unsigned to integer drop typemod,
         type integer when unsigned to integer drop typemod,
         type int TO integer,
         type integer TO integer

    ALTER SCHEMA 'support_mozilla_com' RENAME TO 'public';
"""

SCRIPT_TEMPLATE = """
#!/bin/bash
set -e
{}
"""


def run(prefetch_rows=8000):
    """
    Creates a bash script that performs all of the pgloader runs.
    """
    if not (mysql_pwd := os.getenv("MYSQL_PWD")):
        print("ERROR: Please define MYSQL_PWD!")
        return

    if not (postgres_pwd := os.getenv("POSTGRES_PWD")):
        print("ERROR: Please define POSTGRES_PWD!")
        return

    batch_rows = int(prefetch_rows) // 4

    print("-- make_pgloader_script --")
    print(f"   prefetch={prefetch_rows}, batch={batch_rows}")

    # Generate each of the migration configuration files.
    filepaths = []
    filepath_template = "/tmp/migration.{}.{}.load"
    for i, table in enumerate(TABLES_SINGLES + ["skinny_tables"]):
        path = Path(filepath_template.format(i, table))
        matching = (
            f"'{table}'"
            if table != "skinny_tables"
            else ", ".join(f"'{t}'" for t in TABLES_SKINNY)
        )
        path.write_text(
            CONFIG_TEMPLATE.format(
                mysql_pwd,
                postgres_pwd,
                matching,
                batch_rows,
                prefetch_rows,
            )
        )
        print(f"   {path}")
        filepaths.append(path)

    # Generate the pgloader script.
    script_path = Path("/tmp/run-pgloader.sh")
    cmds = "\n".join(f"pgloader --summary {fp}.summary {fp}" for fp in filepaths)
    script_path.write_text(SCRIPT_TEMPLATE.format(cmds))
    script_path.chmod(stat.S_IXUSR | stat.S_IRUSR | stat.S_IWUSR)
    print(f"created: {script_path}")


if __name__ == "__main__":
    print(
        'Run with "./manage.py runscript make_pgloader_script [--script-args <num-prefetch-rows>]"'
    )
