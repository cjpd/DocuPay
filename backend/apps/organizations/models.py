from django.db import models

from apps.common.models import TimeStampedModel


class Organization(TimeStampedModel):
    name = models.CharField(max_length=255)
    slug = models.SlugField(unique=True)

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
