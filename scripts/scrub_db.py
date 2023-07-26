# Run this with ./manage.py runscript scrub_db [--script-args <database-alias>]

from django.db.models import Value
from django.db.models.functions import Replace

from kitsune.flagit.models import FlaggedObject
from kitsune.questions.models import Answer, Question, QuestionMetaData
from kitsune.wiki.models import HelpfulVoteMetadata


def run(*args):
    """
    Scrubs the database of anything that would cause problems when migrating to PostgreSQL.
    Accepts a single, optional argument that selects the database to scrub, which by default
    is the "default" database.
    """
    database_alias = args[0] if args else "default"
    # Remove NUL characters
    for model, field_name in (
        (Answer, "content"),
        (Question, "content"),
        (QuestionMetaData, "value"),
        (HelpfulVoteMetadata, "value"),
    ):
        # Remove NUL characters from the given field of the given model.
        num_rows = (
            model.objects.using(database_alias)
            .filter(**{f"{field_name}__contains": "\x00"})
            .update(**{field_name: Replace(field_name, Value("\x00"))})
        )
        print(f"{model}.{field_name}: {num_rows} rows scrubbed")

    # Ensure that "FlaggedObject.reason" is never null.
    num_rows = (
        FlaggedObject.objects.using(database_alias)
        .filter(reason__isnull=True)
        .update(reason="other")
    )
    print(f"FlaggedObject.reason: {num_rows} rows scrubbed")


if __name__ == "__main__":
    print('Run with "./manage.py runscript scrub_db [--script-args <database-alias>]"')
