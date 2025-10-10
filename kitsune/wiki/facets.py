import hashlib
from datetime import timedelta

from django.conf import settings
from django.contrib.postgres.aggregates import StringAgg
from django.core.cache import cache
from django.db.models import Count, Exists, F, OuterRef, Q, Subquery
from django.db.models.functions import Coalesce, Now

from kitsune.products.models import Topic
from kitsune.wiki.models import Document


def topics_for(user, product, parent=False):
    """Returns a list of topics that apply to passed in product.

    :arg product: a Product instance
    :arg parent: (optional) limit to topics with the given parent
    """

    docs = Document.objects.visible(
        user,
        locale=settings.WIKI_DEFAULT_LANGUAGE,
        is_archived=False,
        current_revision__isnull=False,
        products=product,
        category__in=settings.IA_DEFAULT_CATEGORIES,
    )

    qs = Topic.active.filter(products=product)
    qs = qs.filter(visible=True, document__in=docs).annotate(num_docs=Count("document")).distinct()

    if parent or parent is None:
        qs = qs.filter(parent=parent)

    return qs


def documents_for(user, locale, topics=None, products=None, current_document=None):
    """Returns a tuple of lists of articles that apply to topics and products.

    The first item in the tuple is the list of articles for the locale
    specified. The second item is the list of fallback articles in en-US
    that aren't localized to the specified locale. If the specified locale
    is en-US, the second item will be None.

    :arg user: the user making this request
    :arg locale: the locale
    :arg topics: (optional) a list of Topic instances
    :arg products: (optional) a list of Product instances
    :arg current_document: (optional) a Document instance to exclude from the results

    The articles are returned as a list of dicts with the following keys:
        id
        document_title
        url
        document_parent_id
    """
    documents = _documents_for(user, locale, topics, products)

    if exclude_current_document := isinstance(current_document, Document):
        if documents and current_document.locale == locale:
            documents = [d for d in documents if d["id"] != current_document.id]

    # For locales that aren't en-US, get the en-US documents
    # to fill in for untranslated articles.
    if locale != settings.WIKI_DEFAULT_LANGUAGE:
        # Start by getting all of the English documents for the given products and topics.
        en_documents = _documents_for(
            user,
            locale=settings.WIKI_DEFAULT_LANGUAGE,
            products=products,
            topics=topics,
        )
        # Exclude the English versions of the translated documents we've already found.
        exclude_en_document_ids = {
            d["document_parent_id"] for d in documents if "document_parent_id" in d
        }
        if exclude_current_document:
            # Exclude the current document if it's in English, or its parent if it's not.
            exclude_en_document_ids.add(
                current_document.parent.id if current_document.parent else current_document.id
            )
        fallback_documents = [d for d in en_documents if d["id"] not in exclude_en_document_ids]
    else:
        fallback_documents = None

    return documents, fallback_documents


def _documents_for(user, locale, topics=None, products=None):
    """Returns a list of articles that apply to passed in locale, topics and products."""
    cache_key = _cache_key(locale, topics, products)

    if not user.is_authenticated:
        # For anonymous users, first check the cache.
        documents_cache_key = f"documents_for_v2:{cache_key}"
        documents = cache.get(documents_cache_key)
        if documents is not None:
            return documents

    qs = Document.objects.visible(
        user,
        locale=locale,
        is_template=False,
        is_archived=False,
        current_revision__isnull=False,
        category__in=settings.IA_DEFAULT_CATEGORIES,
    ).annotate(root_id=Coalesce(F("parent_id"), F("id")))

    if topics:
        qs = qs.filter(
            Exists(
                Document.objects.filter(
                    id=OuterRef("root_id"),
                    topics__in=topics,
                )
            )
        )

    if products:
        qs = qs.filter(
            Exists(
                Document.objects.filter(
                    id=OuterRef("root_id"),
                    products__in=products,
                )
            )
        )

    qs = qs.annotate(
        num_helpful_votes=Count(
            "current_revision__poll_votes",
            filter=Q(
                current_revision__poll_votes__helpful=True,
                current_revision__poll_votes__created__range=(Now() - timedelta(days=30), Now()),
            ),
        ),
        product_titles=Subquery(
            Document.objects.filter(id=OuterRef("root_id"))
            .annotate(
                product_titles=StringAgg(
                    "products__title", delimiter=", ", ordering="products__title"
                ),
            )
            .values("product_titles")[:1]
        ),
        order=Coalesce("parent__display_order", "display_order"),
    )

    qs = qs.select_related("current_revision", "parent").order_by("order", "-num_helpful_votes")

    doc_dicts = []
    for d in qs:
        doc_dicts.append(
            {
                "id": d.id,
                "document_title": d.title,
                "url": d.get_absolute_url(),
                "document_parent_id": d.parent_id,
                "created": d.current_revision.created,
                "product_titles": d.product_titles,
                "document_summary": d.current_revision.summary,
                "display_order": d.original.display_order,
                "helpful_votes": d.num_helpful_votes,
            }
        )

    if not user.is_authenticated:
        cache.set(documents_cache_key, doc_dicts)

    return doc_dicts


def _cache_key(locale, topics, products):
    m = hashlib.md5()
    key = "{locale}:{topics}:{products}:new".format(
        locale=locale,
        topics=",".join(sorted([t.slug for t in topics or []])),
        products=",".join(sorted([p.slug for p in products or []])),
    )

    m.update(key.encode())
    return m.hexdigest()
