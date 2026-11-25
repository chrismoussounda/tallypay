from typing import Any, ClassVar

from django.contrib.auth.base_user import AbstractBaseUser, BaseUserManager
from django.db import models
from django.db.models import Q

from merchants.models import Demo, Merchant


class UserManager(BaseUserManager["User"]):
    def create_user(self, email: str, password: str | None, **fields: Any) -> "User":
        user = self.model(email=email.strip().lower(), **fields)
        user.set_password(password)
        user.save()
        return user

    def get_by_natural_key(self, username: str | None) -> "User":
        return self.get(email=(username or "").strip().lower())


class User(AbstractBaseUser):
    class Role(models.TextChoices):
        OWNER = "owner"
        DEVELOPER = "developer"
        SUPPORT = "support"
        VIEWER = "viewer"
        # Of TallyPay itself, not of a merchant.
        OPERATOR = "operator"

    email = models.EmailField(unique=True)
    name = models.CharField(max_length=80)
    role = models.CharField(max_length=10, choices=Role)
    merchant = models.ForeignKey(
        Merchant,
        on_delete=models.PROTECT,
        related_name="users",
        null=True,
        blank=True,
    )
    # Someone made up for a demo. An operator of one sees nothing else.
    demo = models.ForeignKey(
        Demo, on_delete=models.PROTECT, related_name="people", null=True, blank=True
    )
    is_active = models.BooleanField(default=True)

    objects: ClassVar[UserManager] = UserManager()

    USERNAME_FIELD = "email"
    REQUIRED_FIELDS: ClassVar[list[str]] = ["name", "role"]

    class Meta:
        constraints: ClassVar = [
            models.CheckConstraint(
                condition=Q(role="operator", merchant__isnull=True)
                | Q(
                    role__in=["owner", "developer", "support", "viewer"],
                    merchant__isnull=False,
                ),
                name="user_operator_or_of_a_merchant",
            )
        ]

    def __str__(self) -> str:
        return self.email

    @property
    def is_operator(self) -> bool:
        return self.role == self.Role.OPERATOR

    @property
    def can_refund(self) -> bool:
        return self.role in (self.Role.OWNER, self.Role.DEVELOPER, self.Role.SUPPORT)

    @property
    def can_develop(self) -> bool:
        return self.role in (self.Role.OWNER, self.Role.DEVELOPER)


class Attempt(models.Model):
    """A password that was tried. Forgotten once it was right."""

    email = models.CharField(max_length=254)
    address = models.CharField(max_length=45)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        indexes: ClassVar = [
            models.Index(fields=["email", "created_at"], name="attempt_email"),
            models.Index(fields=["address", "created_at"], name="attempt_address"),
        ]

    def __str__(self) -> str:
        return self.email


class Action(models.Model):
    """What someone did from the dashboard."""

    user = models.ForeignKey(User, on_delete=models.PROTECT)
    what = models.CharField(max_length=40)
    subject = models.CharField(max_length=80)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self) -> str:
        return f"{self.what} {self.subject}"
