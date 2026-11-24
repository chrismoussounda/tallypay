import uuid

from django.db import models

from ledger.models import Account


class Merchant(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(max_length=120)
    # What is owed to the merchant.
    account = models.OneToOneField(Account, on_delete=models.PROTECT)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self) -> str:
        return self.name


class ApiKey(models.Model):
    merchant = models.ForeignKey(
        Merchant, on_delete=models.CASCADE, related_name="keys"
    )
    key_hash = models.CharField(max_length=64, unique=True)
    name = models.CharField(max_length=60, blank=True, db_default="")
    # Enough to recognise a key, too little to use it.
    ending = models.CharField(max_length=4)
    created_at = models.DateTimeField(auto_now_add=True)
    revoked_at = models.DateTimeField(null=True, blank=True)

    def __str__(self) -> str:
        return f"...{self.ending}"
