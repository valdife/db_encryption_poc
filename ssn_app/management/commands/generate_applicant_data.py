"""
Management command to generate applicant test data for range query benchmarks.

This command populates ApplicantEncrypted and ApplicantBaseline tables
with income data for benchmarking the "decrypt-all" problem.

Usage:
    python manage.py generate_applicant_data --count 100000
    python manage.py generate_applicant_data --count 100000 --clear
"""
import random
from decimal import Decimal
from time import perf_counter

from django.core.management.base import BaseCommand, CommandParser
from faker import Faker

from ssn_app.crypto import _get_fernet
from ssn_app.models import ApplicantBaseline, ApplicantEncrypted

BATCH_SIZE_DEFAULT = 1000
INCOME_MIN = 5000
INCOME_MAX = 100000


class Command(BaseCommand):
    help = "Generate applicant test data for range query benchmarks"

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument(
            "--count",
            type=int,
            default=100000,
            help="Number of applicant records to generate (default: 100000)",
        )
        parser.add_argument(
            "--batch-size",
            type=int,
            default=BATCH_SIZE_DEFAULT,
            help=f"Batch size for bulk_create (default: {BATCH_SIZE_DEFAULT})",
        )
        parser.add_argument(
            "--clear",
            action="store_true",
            help="Clear existing applicant data before generating",
        )
        parser.add_argument(
            "--encrypted-only",
            action="store_true",
            help="Only generate ApplicantEncrypted records",
        )
        parser.add_argument(
            "--baseline-only",
            action="store_true",
            help="Only generate ApplicantBaseline records",
        )

    def handle(self, *args, **options) -> None:
        count = options["count"]
        batch_size = options["batch_size"]
        clear = options["clear"]
        encrypted_only = options["encrypted_only"]
        baseline_only = options["baseline_only"]

        fake = Faker()
        Faker.seed(42)
        random.seed(42)

        if clear:
            self.stdout.write("Clearing existing applicant data...")
            ApplicantEncrypted.objects.all().delete()
            ApplicantBaseline.objects.all().delete()

        # Pre-generate income values for consistency
        self.stdout.write(f"Pre-generating {count:,} income values...")
        incomes = [
            Decimal(str(random.randint(INCOME_MIN * 100, INCOME_MAX * 100) / 100))
            for _ in range(count)
        ]

        generate_encrypted = not baseline_only
        generate_baseline = not encrypted_only

        if generate_encrypted:
            self._generate_encrypted(fake, incomes, batch_size)

        if generate_baseline:
            self._generate_baseline(fake, incomes, batch_size)

        self.stdout.write(self.style.SUCCESS("Applicant data generation complete!"))

        # Print distribution stats
        self._print_stats(incomes)

    def _generate_encrypted(
        self, fake: Faker, incomes: list[Decimal], batch_size: int
    ) -> None:
        count = len(incomes)
        self.stdout.write(f"Generating {count:,} ApplicantEncrypted records...")

        fernet = _get_fernet()
        start = perf_counter()

        for batch_start in range(0, count, batch_size):
            batch_end = min(batch_start + batch_size, count)
            batch_incomes = incomes[batch_start:batch_end]

            applicants = []
            for income in batch_incomes:
                # Encrypt income directly (bypass property for bulk performance)
                income_str = str(income.quantize(Decimal("0.01")))
                encrypted_income = fernet.encrypt(income_str.encode()).decode()

                applicants.append(ApplicantEncrypted(
                    name=fake.name(),
                    email=fake.email(),
                    income_ciphertext=encrypted_income,
                ))

            ApplicantEncrypted.objects.bulk_create(applicants)

            progress = batch_end / count * 100
            self.stdout.write(f"  Progress: {batch_end:,}/{count:,} ({progress:.1f}%)")

        elapsed = perf_counter() - start
        rate = count / elapsed

        self.stdout.write(
            f"  ApplicantEncrypted: {count:,} records in {elapsed:.2f}s "
            f"({rate:.0f} records/sec)"
        )

    def _generate_baseline(
        self, fake: Faker, incomes: list[Decimal], batch_size: int
    ) -> None:
        count = len(incomes)
        self.stdout.write(f"Generating {count:,} ApplicantBaseline records...")

        start = perf_counter()

        for batch_start in range(0, count, batch_size):
            batch_end = min(batch_start + batch_size, count)
            batch_incomes = incomes[batch_start:batch_end]

            applicants = []
            for income in batch_incomes:
                applicants.append(ApplicantBaseline(
                    name=fake.name(),
                    email=fake.email(),
                    income=income,
                ))

            ApplicantBaseline.objects.bulk_create(applicants)

            progress = batch_end / count * 100
            self.stdout.write(f"  Progress: {batch_end:,}/{count:,} ({progress:.1f}%)")

        elapsed = perf_counter() - start
        rate = count / elapsed

        self.stdout.write(
            f"  ApplicantBaseline: {count:,} records in {elapsed:.2f}s "
            f"({rate:.0f} records/sec)"
        )

    def _print_stats(self, incomes: list[Decimal]) -> None:
        self.stdout.write("")
        self.stdout.write("Income Distribution:")

        # Count records above common thresholds
        thresholds = [10000, 25000, 50000, 75000]
        for t in thresholds:
            above = sum(1 for i in incomes if i > t)
            pct = above / len(incomes) * 100
            self.stdout.write(f"  > ${t:,}: {above:,} records ({pct:.1f}%)")

