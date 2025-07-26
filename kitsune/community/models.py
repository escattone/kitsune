from django.contrib.auth.models import User
from django.contrib.contenttypes.models import ContentType
from django.db import models

from kitsune.products.models import Product
from kitsune.sumo.models import LocaleField


class DeletedContributionManager(models.Manager):
    def answers_per_user(self, start=None, end=None, locale=None, product=None):
        from kitsune.questions.models import Answer

        filter_kwargs = {"content_type": ContentType.objects.get_for_model(Answer)}

        if start:
            filter_kwargs.update(contribution_timestamp__gte=start)

        if end:
            filter_kwargs.update(contribution_timestamp__lte=end)

        if locale:
            filter_kwargs.update(locale=locale)

        if product:
            filter_kwargs.update(products=product)

        self.filter(**filter_kwargs).values("contributor").annotate(count=models.Count("*"))


class DeletedContribution(models.Model):
    created = models.DateTimeField(auto_now_add=True)

    # Contribution-related fields
    content_type = models.ForeignKey(ContentType, on_delete=models.CASCADE)
    contributor = models.ForeignKey(
        User, on_delete=models.CASCADE, related_name="deleted_contributions"
    )
    contribution_timestamp = models.DateTimeField()
    locale = LocaleField()
    products = models.ManyToManyField(Product)

    objects = DeletedContributionManager()

    class Meta:
        indexes = [
            models.Index(fields=["locale"]),
            models.Index(fields=["contribution_timestamp"]),
        ]
