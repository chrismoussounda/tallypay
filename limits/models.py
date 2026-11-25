from django.db import models


class Bucket(models.Model):
    name = models.CharField(max_length=80, primary_key=True)
    # When it will be as if nothing had been asked.
    clear_at = models.DateTimeField()

    def __str__(self) -> str:
        return self.name
