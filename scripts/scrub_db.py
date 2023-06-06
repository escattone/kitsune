# Run this with ./manage.py runscript scrub_db

from django.db.models import Value
from django.db.models.functions import Replace

from kitsune.flagit.models import FlaggedObject
from kitsune.questions.models import Answer, Question, QuestionMetaData
from kitsune.wiki.models import HelpfulVoteMetadata


def run():
    """
    Scrubs the database of anything that would cause problems when migrating to PostgreSQL.
    """
    # Remove NUL characters
    for model, field_name in (
        (Answer, "content"),
        (Question, "content"),
        (QuestionMetaData, "value"),
        (HelpfulVoteMetadata, "value"),
    ):
        # Remove NUL characters from the given field of the given model.
        num_rows = model.objects.filter(**{f"{field_name}__contains": "\x00"}).update(
            **{field_name: Replace(field_name, Value("\x00"))}
        )
        print(f"{model}.{field_name}: {num_rows} rows scrubbed")

    # Ensure that "FlaggedObject.reason" is never null.
    num_rows = FlaggedObject.objects.filter(reason__isnull=True).update(reason="other")
    print(f"FlaggedObject.reason: {num_rows} rows scrubbed")


if __name__ == "__main__":
    print('Run with "./manage.py runscript scrub_db"')
