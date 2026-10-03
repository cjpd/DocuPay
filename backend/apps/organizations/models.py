from django.db import models

from apps.common.models import TimeStampedModel


class Organization(TimeStampedModel):
    name = models.CharField(max_length=255)
    slug = models.SlugField(unique=True)
    auto_approve_threshold = models.FloatField(
        default=0.92,
        help_text="Overall confidence threshold to auto-approve without human review",
    )
    auto_approve_max_amount = models.DecimalField(
        max_digits=14, decimal_places=2, null=True, blank=True,
        help_text="Invoices with a total above this amount always go to human review. Empty = no limit.",
    )

    def __str__(self) -> str:
        return self.name


class OrgMembership(TimeStampedModel):
    organization = models.ForeignKey(Organization, on_delete=models.CASCADE, related_name="memberships")
    user = models.ForeignKey("users.CustomUser", on_delete=models.CASCADE, related_name="memberships")
    role = models.CharField(max_length=50, default="member")

    class Meta:
        unique_together = ("organization", "user")

    def __str__(self) -> str:
        return f"{self.user} in {self.organization} ({self.role})"
