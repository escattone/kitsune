import hashlib
from datetime import date, datetime, timedelta

from django.conf import settings
from django.contrib.auth.models import Group
from django.contrib.contenttypes.models import ContentType
from django.core.cache import cache
from django.db.models import (
    Count,
    DateTimeField,
    Exists,
    F,
    IntegerField,
    Max,
    OuterRef,
    Q,
    Subquery,
)
from django.db.models.functions import Coalesce, Greatest, Now

from kitsune.community.models import DeletedContribution
from kitsune.products.models import Product
from kitsune.questions.models import Answer
from kitsune.users.models import ContributionAreas, Profile, User
from kitsune.users.templatetags.jinja_helpers import profile_avatar
from kitsune.wiki.models import Revision

DEFAULT_PERIOD_DAYS = 90


def top_contributors_questions(start=None, end=None, locale=None, product=None, count=10, page=1):
    """Get the top Support Forum contributors."""

    if not start:
        start = datetime.now() - timedelta(days=DEFAULT_PERIOD_DAYS)

    answers_filters = {}
    deleted_answers_filters = {}

    if locale:
        answers_filters.update(question__locale=locale)
        deleted_answers_filters.update(locale=locale)

    if product:
        answers_filters.update(question__product=product)
        deleted_answers_filters.update(products__in=[product])

    contributor_group_names = ContributionAreas.get_groups()

    qs_answers = Answer.objects.filter(
        is_spam=False,
        created__range=(start, end or Now()),
        **answers_filters,
    )

    qs_deleted_answers = DeletedContribution.objects.filter(
        contribution_timestamp__range=(start, end or Now()),
        content_type=ContentType.objects.get_for_model(Answer),
        **deleted_answers_filters,
    )

    qs_contributors_of_answers = (
        qs_answers.filter(
            creator__is_active=True,
            creator__groups__name__in=contributor_group_names,
        )
        .exclude(creator=F("question__creator"))
        .exclude(creator__profile__account_type=Profile.AccountType.SYSTEM)
        .values_list("creator", flat=True)
        .distinct()
    )

    qs_contributors_of_deleted_answers = (
        qs_deleted_answers.filter(
            contributor__is_active=True,
            contributor__groups__name__in=contributor_group_names,
        )
        .values_list("contributor", flat=True)
        .distinct()
    )

    qs_top_contributors = (
        User.objects.filter(
            Q(id__in=qs_contributors_of_answers) | Q(id__in=qs_contributors_of_deleted_answers)
        )
        .annotate(
            answer_count=Subquery(
                (
                    qs_answers.filter(creator=OuterRef("pk"))
                    .order_by()
                    .values("creator")
                    .annotate(total=Count("*"))
                    .values("total")[:1]
                ),
                output_field=IntegerField(),
            ),
            last_answer_activity=Subquery(
                (
                    qs_answers.filter(creator=OuterRef("pk"))
                    .order_by()
                    .values("creator")
                    .annotate(last_activity=Max("created"))
                    .values("last_activity")[:1]
                ),
                output_field=DateTimeField(),
            ),
            deleted_answer_count=Subquery(
                (
                    qs_deleted_answers.filter(contributor=OuterRef("pk"))
                    .order_by()
                    .values("contributor")
                    .annotate(total=Count("*"))
                    .values("total")[:1]
                ),
                output_field=IntegerField(),
            ),
            last_deleted_answer_activity=Subquery(
                (
                    qs_deleted_answers.filter(contributor=OuterRef("pk"))
                    .order_by()
                    .values("contributor")
                    .annotate(last_activity=Max("contribution_timestamp"))
                    .values("last_activity")[:1]
                ),
                output_field=DateTimeField(),
            ),
        )
        .annotate(
            count=(Coalesce("answer_count", 0) + Coalesce("deleted_answer_count", 0)),
            last_activity=Greatest(
                Coalesce(F("last_answer_activity"), F("last_deleted_answer_activity")),
                Coalesce(F("last_deleted_answer_activity"), F("last_answer_activity")),
            ),
        )
        .select_related("profile")
        .order_by("-count", "id")
    )

    top_contributors = []
    offset = count * (page - 1)
    for user in qs_top_contributors[offset : offset + count]:
        top_contributors.append(
            {
                "count": user.count,
                "term": user.id,
                "user": {
                    "id": user.id,
                    "username": user.username,
                    "display_name": user.profile.name,
                    "avatar": user.profile.fxa_avatar,
                    "days_since_last_activity": (datetime.now() - user.last_activity).days,
                },
            }
        )

    return top_contributors, qs_top_contributors.count()


def top_contributors_kb(**kwargs):
    """Get the top KB editors (locale='en-US')."""
    kwargs["locale"] = settings.WIKI_DEFAULT_LANGUAGE
    return top_contributors_l10n(**kwargs)


def top_contributors_l10n(
    start=None, end=None, locale=None, product=None, count=10, page=1, use_cache=True
):
    """Get the top l10n contributors for the KB."""
    if use_cache:
        cache_key = "{}_{}_{}_{}_{}_{}".format(start, end, locale, product, count, page)
        cache_key = hashlib.sha1(cache_key.encode("utf-8")).hexdigest()
        cache_key = "top_contributors_l10n_{}".format(cache_key)
        cached = cache.get(cache_key, None)
        if cached:
            return cached

    if start is None:
        start = date.today() - timedelta(days=DEFAULT_PERIOD_DAYS)

    # Get the user ids and contribution count of the top contributors.
    revisions = Revision.objects.all()
    revisions = revisions.filter(created__range=(start, end or Now()))

    deleted_revisions = DeletedContribution.objects.filter(
        contributor=OuterRef("pk"),
        contribution_timestamp__range=(start, end or Now()),
        content_type=ContentType.objects.get_for_model(Revision),
    )

    if locale:
        revisions = revisions.filter(document__locale=locale)
        deleted_revisions = deleted_revisions.filter(locale=locale)
    else:
        # If there is no locale specified, exclude en-US only. The rest are l10n.
        revisions = revisions.exclude(document__locale=settings.WIKI_DEFAULT_LANGUAGE)
        deleted_revisions = deleted_revisions.exclude(locale=settings.WIKI_DEFAULT_LANGUAGE)

    if product:
        if isinstance(product, Product):
            product = product.slug
        revisions = revisions.filter(
            Q(document__products__slug=product) | Q(document__parent__products__slug=product)
        )
        deleted_revisions = deleted_revisions.filter(products__slug=product)

    users = (
        User.objects.filter(is_active=True)
        .filter(Q(created_revisions__in=revisions) | Exists(deleted_revisions))
        .annotate(
            deleted_revision_count=Subquery(
                (
                    deleted_revisions.order_by()
                    .values("contributor")
                    .annotate(total=Count("*"))
                    .values("total")[:1]
                ),
                output_field=IntegerField(),
            ),
            created_revision_count=Count(
                "created_revisions", filter=Q(created_revisions__in=revisions)
            ),
        )
        .annotate(
            query_count=(
                Coalesce("created_revision_count", 0) + Coalesce("deleted_revision_count", 0)
            )
        )
        .order_by("-query_count", "id")
        .select_related("profile")
    )

    total = users.count()

    results = [
        {
            "term": user.pk,
            "count": user.query_count,
            "user": {
                "id": user.pk,
                "username": user.username,
                "display_name": user.profile.display_name,
                "avatar": profile_avatar(user),
            },
        }
        for user in users[(page - 1) * count : page * count]
    ]

    if use_cache:
        cache.set(cache_key, (results, total), settings.CACHE_MEDIUM_TIMEOUT)
    return results, total


def num_deleted_contributions(model, exclude_locale=None, **filters):
    """
    Returns the number of deleted model instances scoped by the filters.
    """
    qs = DeletedContribution.objects.filter(
        content_type=ContentType.objects.get_for_model(model), **filters
    )
    if exclude_locale:
        qs = qs.exclude(locale=exclude_locale)

    if any(key in filters for key in ("products__in", "contributor__groups__in")):
        qs = qs.distinct()

    return qs.count()


def deleted_contribution_metrics_by_contributor(
    model,
    start=None,
    end=None,
    locale=None,
    products=None,
    max_results=None,
    limit_to_contributor_groups=False,
    **extra_filters,
):
    use_distinct = False

    filter_kwargs = {"content_type": ContentType.objects.get_for_model(model)}

    if start:
        filter_kwargs.update(contribution_timestamp__gte=start)

    if end:
        filter_kwargs.update(contribution_timestamp__lte=end)

    if locale:
        filter_kwargs.update(locale=locale)

    if products:
        use_distinct = True
        filter_kwargs.update(products__in=products)

    if limit_to_contributor_groups:
        use_distinct = True
        filter_kwargs.update(
            contributor__groups__in=Group.objects.filter(
                name__in=ContributionAreas.get_groups(),
            ),
        )

    filter_kwargs.update(extra_filters)

    qs = DeletedContribution.objects.filter(**filter_kwargs)

    if use_distinct:
        qs = qs.distinct()

    results = (
        qs.values("contributor")
        .annotate(
            total_deleted_contributions=Count("*"),
            last_contribution_timestamp=Max("contribution_timestamp"),
        )
        .order_by("-total_deleted_contributions", "contributor")
    )

    if max_results:
        results = results[:max_results]

    return {
        row["contributor"]: (
            row["total_deleted_contributions"],
            row["last_contribution_timestamp"],
        )
        for row in results
    }
