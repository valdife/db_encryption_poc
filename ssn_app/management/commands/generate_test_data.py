"""
Management command to generate test data for SSN encryption benchmarking.

This command populates both PersonEncrypted and PersonBaseline tables with
synthetic data for performance testing. It uses bulk_create for efficiency
and can generate hundreds of thousands or millions of records.

Usage:
    python manage.py generate_test_data --count 100000
    python manage.py generate_test_data --count 1000000 --batch-size 5000
    python manage.py generate_test_data --count 100000 --with-orders --orders-per-person 3
"""
import random
from decimal import Decimal
from time import perf_counter

from django.core.management.base import BaseCommand, CommandParser
from faker import Faker

from ssn_app.crypto import encrypt_ssn, hash_ssn, normalize_ssn
from ssn_app.models import Order, PersonBaseline, PersonEncrypted

BATCH_SIZE_DEFAULT = 1000


class Command(BaseCommand):
    help = "Generate test data for SSN encryption benchmarking"

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument(
            "--count",
            type=int,
            default=10000,
            help="Number of Person records to generate (default: 10000)",
        )
        parser.add_argument(
            "--batch-size",
            type=int,
            default=BATCH_SIZE_DEFAULT,
            help=f"Batch size for bulk_create (default: {BATCH_SIZE_DEFAULT})",
        )
        parser.add_argument(
            "--with-orders",
            action="store_true",
            help="Also generate Order records linked to each Person",
        )
        parser.add_argument(
            "--orders-per-person",
            type=int,
            default=2,
            help="Number of orders per person when --with-orders is used (default: 2)",
        )
        parser.add_argument(
            "--encrypted-only",
            action="store_true",
            help="Only generate PersonEncrypted records",
        )
        parser.add_argument(
            "--baseline-only",
            action="store_true",
            help="Only generate PersonBaseline records",
        )
        parser.add_argument(
            "--clear",
            action="store_true",
            help="Clear existing data before generating new data",
        )

    def handle(self, *args, **options) -> None:
        count = options["count"]
        batch_size = options["batch_size"]
        with_orders = options["with_orders"]
        orders_per_person = options["orders_per_person"]
        encrypted_only = options["encrypted_only"]
        baseline_only = options["baseline_only"]
        clear = options["clear"]

        fake = Faker()
        Faker.seed(42)  # Reproducible data
        random.seed(42)

        if clear:
            self._clear_data()

        generate_encrypted = not baseline_only
        generate_baseline = not encrypted_only

        # Pre-generate SSNs to use the same ones for both tables
        self.stdout.write(f"Pre-generating {count} SSNs...")
        ssns = [self._generate_ssn() for _ in range(count)]

        # Store created IDs for order generation
        encrypted_ids: list[int] = []
        baseline_ids: list[int] = []

        if generate_encrypted:
            encrypted_ids = self._generate_encrypted(
                fake, ssns, count, batch_size
            )

        if generate_baseline:
            baseline_ids = self._generate_baseline(
                fake, ssns, count, batch_size
            )

        if with_orders and (encrypted_ids or baseline_ids):
            self._generate_orders(
                fake, encrypted_ids, baseline_ids, orders_per_person, batch_size
            )

        self.stdout.write(self.style.SUCCESS("Data generation complete!"))

    def _clear_data(self) -> None:
        self.stdout.write("Clearing existing data...")
        Order.objects.all().delete()
        PersonEncrypted.objects.all().delete()
        PersonBaseline.objects.all().delete()
        self.stdout.write("Existing data cleared.")

    def _generate_ssn(self) -> str:
        """Generate a random 9-digit SSN."""
        # Avoid SSNs starting with 000, 666, or 900-999 (invalid per SSA rules)
        area = random.randint(1, 665) if random.random() > 0.1 else random.randint(667, 899)
        group = random.randint(1, 99)
        serial = random.randint(1, 9999)
        return f"{area:03d}{group:02d}{serial:04d}"

    def _generate_encrypted(
        self, fake: Faker, ssns: list[str], count: int, batch_size: int
    ) -> list[int]:
        self.stdout.write(f"Generating {count} PersonEncrypted records...")
        start = perf_counter()

        created_ids: list[int] = []

        for batch_start in range(0, count, batch_size):
            batch_end = min(batch_start + batch_size, count)
            batch_ssns = ssns[batch_start:batch_end]

            persons = []
            for ssn in batch_ssns:
                # Pre-compute encryption and hash for bulk_create
                # (property setter doesn't work with bulk_create)
                persons.append(PersonEncrypted(
                    first_name=fake.first_name(),
                    last_name=fake.last_name(),
                    email=fake.email(),
                    ssn_ciphertext=encrypt_ssn(ssn),
                    ssn_hash=hash_ssn(ssn),
                ))

            created = PersonEncrypted.objects.bulk_create(persons)
            created_ids.extend(p.id for p in created)

            progress = batch_end / count * 100
            self.stdout.write(f"  Progress: {batch_end}/{count} ({progress:.1f}%)")

        elapsed = perf_counter() - start
        rate = count / elapsed

        self.stdout.write(
            f"  PersonEncrypted: {count} records in {elapsed:.2f}s "
            f"({rate:.0f} records/sec)"
        )

        return created_ids

    def _generate_baseline(
        self, fake: Faker, ssns: list[str], count: int, batch_size: int
    ) -> list[int]:
        self.stdout.write(f"Generating {count} PersonBaseline records...")
        start = perf_counter()

        created_ids: list[int] = []

        for batch_start in range(0, count, batch_size):
            batch_end = min(batch_start + batch_size, count)
            batch_ssns = ssns[batch_start:batch_end]

            persons = []
            for ssn in batch_ssns:
                persons.append(PersonBaseline(
                    first_name=fake.first_name(),
                    last_name=fake.last_name(),
                    email=fake.email(),
                    ssn=normalize_ssn(ssn),
                ))

            created = PersonBaseline.objects.bulk_create(persons)
            created_ids.extend(p.id for p in created)

            progress = batch_end / count * 100
            self.stdout.write(f"  Progress: {batch_end}/{count} ({progress:.1f}%)")

        elapsed = perf_counter() - start
        rate = count / elapsed

        self.stdout.write(
            f"  PersonBaseline: {count} records in {elapsed:.2f}s "
            f"({rate:.0f} records/sec)"
        )

        return created_ids

    def _generate_orders(
        self,
        fake: Faker,
        encrypted_ids: list[int],
        baseline_ids: list[int],
        orders_per_person: int,
        batch_size: int,
    ) -> None:
        total_orders = max(len(encrypted_ids), len(baseline_ids)) * orders_per_person
        self.stdout.write(f"Generating approximately {total_orders} Order records...")
        start = perf_counter()

        orders: list[Order] = []
        order_counter = 0

        # Generate orders for encrypted persons
        for person_id in encrypted_ids:
            for _ in range(orders_per_person):
                order_counter += 1
                orders.append(Order(
                    order_number=f"ORD-E-{order_counter:010d}",
                    amount=Decimal(str(round(random.uniform(10, 1000), 2))),
                    person_encrypted_id=person_id,
                    person_baseline_id=None,
                ))

                if len(orders) >= batch_size:
                    Order.objects.bulk_create(orders)
                    orders = []

        # Generate orders for baseline persons
        for person_id in baseline_ids:
            for _ in range(orders_per_person):
                order_counter += 1
                orders.append(Order(
                    order_number=f"ORD-B-{order_counter:010d}",
                    amount=Decimal(str(round(random.uniform(10, 1000), 2))),
                    person_encrypted_id=None,
                    person_baseline_id=person_id,
                ))

                if len(orders) >= batch_size:
                    Order.objects.bulk_create(orders)
                    orders = []

        # Final batch
        if orders:
            Order.objects.bulk_create(orders)

        elapsed = perf_counter() - start
        rate = order_counter / elapsed

        self.stdout.write(
            f"  Orders: {order_counter} records in {elapsed:.2f}s "
            f"({rate:.0f} records/sec)"
        )

