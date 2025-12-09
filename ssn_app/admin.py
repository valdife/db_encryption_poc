"""
Django admin configuration for SSN models.

Security considerations:
- ssn_ciphertext is excluded from admin forms to prevent accidental exposure
- Only masked SSN is displayed in list views
- Plaintext SSN can only be set through forms, never displayed in full
"""
from django.contrib import admin

from ssn_app.models import (
    ApplicantBaseline,
    ApplicantEncrypted,
    Order,
    PersonBaseline,
    PersonEncrypted,
)


@admin.register(PersonEncrypted)
class PersonEncryptedAdmin(admin.ModelAdmin):
    list_display = ["id", "first_name", "last_name", "email", "ssn_masked", "created_at"]
    list_filter = ["created_at"]
    search_fields = ["first_name", "last_name", "email"]
    readonly_fields = ["ssn_masked", "ssn_hash", "created_at", "updated_at"]

    # Exclude ciphertext from forms - it should never be directly edited
    exclude = ["ssn_ciphertext"]

    fieldsets = [
        (None, {
            "fields": ["first_name", "last_name", "email"]
        }),
        ("SSN (Read Only)", {
            "fields": ["ssn_masked", "ssn_hash"],
            "description": "SSN is encrypted at rest. Only masked version is displayed.",
        }),
        ("Timestamps", {
            "fields": ["created_at", "updated_at"],
            "classes": ["collapse"],
        }),
    ]

    def ssn_masked(self, obj: PersonEncrypted) -> str:
        return obj.ssn_masked

    ssn_masked.short_description = "SSN (Masked)"


@admin.register(PersonBaseline)
class PersonBaselineAdmin(admin.ModelAdmin):
    """Admin for baseline model - for benchmarking only."""
    list_display = ["id", "first_name", "last_name", "email", "ssn", "created_at"]
    list_filter = ["created_at"]
    search_fields = ["first_name", "last_name", "email", "ssn"]


@admin.register(Order)
class OrderAdmin(admin.ModelAdmin):
    list_display = ["order_number", "amount", "person_encrypted", "person_baseline", "created_at"]
    list_filter = ["created_at"]
    search_fields = ["order_number"]
    raw_id_fields = ["person_encrypted", "person_baseline"]


@admin.register(ApplicantEncrypted)
class ApplicantEncryptedAdmin(admin.ModelAdmin):
    """Admin for encrypted applicant model - demonstrates decrypt-all problem."""
    list_display = ["id", "name", "email", "created_at"]
    list_filter = ["created_at"]
    search_fields = ["name", "email"]
    readonly_fields = ["income_ciphertext", "created_at"]
    exclude = ["income_ciphertext"]


@admin.register(ApplicantBaseline)
class ApplicantBaselineAdmin(admin.ModelAdmin):
    """Admin for baseline applicant model - for benchmarking only."""
    list_display = ["id", "name", "email", "income", "created_at"]
    list_filter = ["created_at"]
    search_fields = ["name", "email"]

