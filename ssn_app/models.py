"""
Django models demonstrating column-level encryption strategies.

This module provides model variants for performance comparison:

1. PersonEncrypted: SSN with hash for equality queries (RECOMMENDED for SSN)
   - ssn_ciphertext: Encrypted SSN (Fernet/AES)
   - ssn_hash: Salted SHA-256 hash for equality queries (indexed)

2. PersonBaseline: Plain SSN storage for performance baseline

3. ApplicantEncrypted: Income encryption demonstrating the "decrypt-all" problem
   - income_ciphertext: Encrypted income (Fernet/AES)
   - NO hash because range queries (>, <) and sorting are needed

4. ApplicantBaseline: Plain income storage for comparison

The Applicant models demonstrate WHY encrypting fields that need range/sort
queries is problematic - you must decrypt ALL records in the application layer.
"""
from decimal import Decimal

from django.db import models

from ssn_app.crypto import decrypt_ssn, encrypt_ssn, hash_ssn, mask_ssn


class PersonEncrypted(models.Model):
    """
    Person model with encrypted SSN storage.

    The SSN is stored in two columns:
    - ssn_ciphertext: The encrypted SSN for secure storage and retrieval
    - ssn_hash: A salted hash of the SSN for efficient equality queries

    Usage:
        # Creating a record
        person = PersonEncrypted(first_name="John", last_name="Doe", email="john@example.com")
        person.ssn = "123-45-6789"  # Automatically encrypts and hashes
        person.save()

        # Querying by SSN
        from ssn_app.crypto import hash_ssn
        person = PersonEncrypted.objects.filter(ssn_hash=hash_ssn("123-45-6789")).first()

        # Retrieving SSN
        print(person.ssn)  # Automatically decrypts: "123456789"
    """
    first_name = models.CharField(max_length=100)
    last_name = models.CharField(max_length=100)
    email = models.EmailField(db_index=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    # Encrypted SSN storage
    ssn_ciphertext = models.TextField(
        help_text="Encrypted SSN (Fernet). Never access directly."
    )
    ssn_hash = models.CharField(
        max_length=64,
        db_index=True,
        help_text="SHA-256 hash of SSN for equality queries."
    )

    class Meta:
        db_table = "person_encrypted"
        verbose_name = "Person (Encrypted)"
        verbose_name_plural = "People (Encrypted)"
        indexes = [
            models.Index(fields=["last_name", "first_name"]),
        ]

    def __str__(self) -> str:
        return f"{self.first_name} {self.last_name}"

    @property
    def ssn(self) -> str | None:
        """Decrypt and return the SSN."""
        if not self.ssn_ciphertext:
            return None
        return decrypt_ssn(self.ssn_ciphertext)

    @ssn.setter
    def ssn(self, value: str) -> None:
        """Encrypt the SSN and compute its hash."""
        if value is None:
            self.ssn_ciphertext = ""
            self.ssn_hash = ""
            return
        self.ssn_ciphertext = encrypt_ssn(value)
        self.ssn_hash = hash_ssn(value)

    @property
    def ssn_masked(self) -> str:
        """Return masked SSN for display (e.g., ***-**-6789)."""
        if not self.ssn_ciphertext:
            return "***-**-****"
        try:
            return mask_ssn(self.ssn)
        except Exception:
            return "***-**-****"


class PersonBaseline(models.Model):
    """
    Baseline Person model with plain SSN storage.

    WARNING: This model stores SSN in plain text and should NEVER be used
    in production with real SSN data. It exists solely for performance
    benchmarking to compare against the encrypted variant.
    """
    first_name = models.CharField(max_length=100)
    last_name = models.CharField(max_length=100)
    email = models.EmailField(db_index=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    # Plain SSN storage (FOR BENCHMARKING ONLY)
    ssn = models.CharField(
        max_length=9,
        db_index=True,
        help_text="Plain SSN (FOR BENCHMARKING ONLY - never use in production)"
    )

    class Meta:
        db_table = "person_baseline"
        verbose_name = "Person (Baseline)"
        verbose_name_plural = "People (Baseline)"
        indexes = [
            models.Index(fields=["last_name", "first_name"]),
        ]

    def __str__(self) -> str:
        return f"{self.first_name} {self.last_name}"


class Order(models.Model):
    """
    Order model to demonstrate join performance with encrypted models.

    This model has foreign keys to both PersonEncrypted and PersonBaseline
    to enable benchmarking of join queries.
    """
    order_number = models.CharField(max_length=50, unique=True)
    amount = models.DecimalField(max_digits=10, decimal_places=2)
    created_at = models.DateTimeField(auto_now_add=True)

    # Relations to both model variants for benchmarking
    person_encrypted = models.ForeignKey(
        PersonEncrypted,
        on_delete=models.CASCADE,
        related_name="orders",
        null=True,
        blank=True,
    )
    person_baseline = models.ForeignKey(
        PersonBaseline,
        on_delete=models.CASCADE,
        related_name="orders",
        null=True,
        blank=True,
    )

    class Meta:
        db_table = "orders"
        indexes = [
            models.Index(fields=["created_at"]),
        ]

    def __str__(self) -> str:
        return self.order_number


class ApplicantEncrypted(models.Model):
    """
    Applicant model with encrypted income - demonstrates the "decrypt-all" problem.

    This model encrypts the income field, but unlike SSN:
    - Income needs range queries (WHERE income > 10000)
    - Income needs sorting (ORDER BY income DESC)
    - We CANNOT use a hash because hashes don't preserve order

    This means ANY query involving income filtering or sorting requires:
    1. Fetching ALL records from DB
    2. Decrypting ALL income values in application
    3. Filtering/sorting in Python
    4. This is O(n) instead of O(log n) with indexes

    This model exists to demonstrate WHY you should NOT encrypt fields
    that require range queries or sorting, unless you accept the performance cost.
    """
    name = models.CharField(max_length=200)
    email = models.EmailField(db_index=True)
    created_at = models.DateTimeField(auto_now_add=True)

    # Encrypted income - NO INDEX POSSIBLE for range/sort queries
    income_ciphertext = models.TextField(
        help_text="Encrypted income. Range queries require decrypt-all."
    )

    class Meta:
        db_table = "applicant_encrypted"
        verbose_name = "Applicant (Encrypted Income)"
        verbose_name_plural = "Applicants (Encrypted Income)"

    def __str__(self) -> str:
        return self.name

    @property
    def income(self) -> Decimal | None:
        """Decrypt and return the income."""
        if not self.income_ciphertext:
            return None
        from ssn_app.crypto import _get_fernet
        fernet = _get_fernet()
        decrypted = fernet.decrypt(self.income_ciphertext.encode()).decode()
        return Decimal(decrypted)

    @income.setter
    def income(self, value: Decimal | int | float | str) -> None:
        """Encrypt the income value."""
        if value is None:
            self.income_ciphertext = ""
            return
        from ssn_app.crypto import _get_fernet
        fernet = _get_fernet()
        # Store as string with 2 decimal places for consistency
        value_str = str(Decimal(str(value)).quantize(Decimal("0.01")))
        self.income_ciphertext = fernet.encrypt(value_str.encode()).decode()


class ApplicantBaseline(models.Model):
    """
    Baseline Applicant model with plain income storage.

    This model allows PostgreSQL to:
    - Use B-tree index for range queries (WHERE income > 10000)
    - Use index for sorting (ORDER BY income DESC)
    - Execute LIMIT efficiently (no need to fetch all rows)

    Query: SELECT * FROM applicant WHERE income > 10000 ORDER BY income DESC LIMIT 50
    - With index: O(log n) to find starting point, then stream 50 rows
    - Encrypted: O(n) to fetch all, decrypt all, sort all, take 50
    """
    name = models.CharField(max_length=200)
    email = models.EmailField(db_index=True)
    created_at = models.DateTimeField(auto_now_add=True)

    # Plain income with index - enables efficient range queries and sorting
    income = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        db_index=True,
        help_text="Plain income (FOR BENCHMARKING ONLY)"
    )

    class Meta:
        db_table = "applicant_baseline"
        verbose_name = "Applicant (Baseline)"
        verbose_name_plural = "Applicants (Baseline)"

    def __str__(self) -> str:
        return self.name

