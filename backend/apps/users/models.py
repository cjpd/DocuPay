from django.contrib.auth.models import AbstractUser


class CustomUser(AbstractUser):
    """
    Custom user placeholder for org-aware roles; extend as needed.
    """

    def __str__(self) -> str:
        return self.username
