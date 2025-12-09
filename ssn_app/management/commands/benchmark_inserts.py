"""
Management command to benchmark insert performance with encryption overhead.

This command measures the write performance difference between encrypted
and non-encrypted SSN storage, isolating the encryption/hashing overhead.

Usage:
    python manage.py benchmark_inserts
    python manage.py benchmark_inserts --count 10000
    python manage.py benchmark_inserts --output insert_results.json
"""
import json
import random
from time import perf_counter

from django.core.management.base import BaseCommand, CommandParser
from django.db import connection
from faker import Faker

from ssn_app.crypto import encrypt_ssn, hash_ssn, normalize_ssn
from ssn_app.models import PersonBaseline, PersonEncrypted

COUNT_DEFAULT = 1000
BATCH_SIZE = 100


class Command(BaseCommand):
    help = "Benchmark insert performance with and without encryption"

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument(
            "--count",
            type=int,
            default=COUNT_DEFAULT,
            help=f"Number of records to insert per test (default: {COUNT_DEFAULT})",
        )
        parser.add_argument(
            "--output",
            type=str,
            help="Output results to JSON file",
        )

    def handle(self, *args, **options) -> None:
        count = options["count"]
        output_file = options["output"]

        fake = Faker()
        Faker.seed(12345)
        random.seed(12345)

        self.stdout.write(f"Benchmarking insert performance with {count} records...")
        self.stdout.write("=" * 70)

        # Pre-generate test data
        self.stdout.write("Generating test data in memory...")
        test_data = []
        for _ in range(count):
            area = random.randint(1, 665)
            group = random.randint(1, 99)
            serial = random.randint(1, 9999)
            ssn = f"{area:03d}{group:02d}{serial:04d}"

            test_data.append({
                "first_name": fake.first_name(),
                "last_name": fake.last_name(),
                "email": fake.email(),
                "ssn": ssn,
            })

        # Benchmark 1: Measure crypto operations only (no DB)
        self.stdout.write("\n1. Crypto operations overhead (no database):")
        crypto_time = self._benchmark_crypto_only(test_data)

        # Benchmark 2: Encrypted inserts
        self.stdout.write("\n2. Encrypted inserts (crypto + DB):")
        encrypted_time = self._benchmark_encrypted_inserts(test_data)

        # Benchmark 3: Baseline inserts
        self.stdout.write("\n3. Baseline inserts (DB only):")
        baseline_time = self._benchmark_baseline_inserts(test_data)

        # Summary
        self.stdout.write("\n" + "=" * 70)
        self.stdout.write("SUMMARY")
        self.stdout.write("=" * 70)

        crypto_pct = (crypto_time / encrypted_time * 100) if encrypted_time > 0 else 0
        overhead_ms = encrypted_time - baseline_time
        overhead_pct = (overhead_ms / baseline_time * 100) if baseline_time > 0 else 0

        self.stdout.write(f"\nTotal time to insert {count} records:")
        self.stdout.write(f"  Encrypted: {encrypted_time:.2f}ms ({encrypted_time/count:.3f}ms per record)")
        self.stdout.write(f"  Baseline:  {baseline_time:.2f}ms ({baseline_time/count:.3f}ms per record)")
        self.stdout.write(f"  Overhead:  {overhead_ms:+.2f}ms ({overhead_pct:+.1f}%)")

        self.stdout.write(f"\nCrypto-only time: {crypto_time:.2f}ms ({crypto_pct:.1f}% of encrypted insert time)")

        self.stdout.write("\n" + "=" * 70)
        self.stdout.write("INTERPRETATION")
        self.stdout.write("=" * 70)
        self.stdout.write(f"""
The encryption overhead for {count} inserts is {overhead_ms:.0f}ms ({overhead_pct:.1f}%).

Breakdown:
- Pure crypto operations (encrypt + hash) take {crypto_time:.0f}ms total
- This is {crypto_time/count:.3f}ms per record
- DB I/O accounts for the majority of insert time

For write-heavy workloads:
- At {count/overhead_ms*1000:.0f} encrypted inserts/second capability
- Overhead is {'acceptable' if overhead_pct < 50 else 'significant'} for most use cases

Recommendations:
- Use bulk_create for batch inserts to amortize connection overhead
- Pre-compute encryption/hashes before bulk operations
- Consider async encryption for very high-throughput scenarios
""")

        # Save results
        if output_file:
            results = {
                "count": count,
                "crypto_only_ms": round(crypto_time, 3),
                "encrypted_insert_ms": round(encrypted_time, 3),
                "baseline_insert_ms": round(baseline_time, 3),
                "overhead_ms": round(overhead_ms, 3),
                "overhead_percent": round(overhead_pct, 1),
            }
            with open(output_file, "w") as f:
                json.dump(results, f, indent=2)
            self.stdout.write(f"\nResults saved to {output_file}")

    def _benchmark_crypto_only(self, test_data: list[dict]) -> float:
        """Measure time for encryption + hashing only (no DB)."""
        start = perf_counter()

        for record in test_data:
            _ = encrypt_ssn(record["ssn"])
            _ = hash_ssn(record["ssn"])

        elapsed_ms = (perf_counter() - start) * 1000

        self.stdout.write(f"  Crypto time: {elapsed_ms:.2f}ms for {len(test_data)} records")
        self.stdout.write(f"  Per record: {elapsed_ms/len(test_data):.3f}ms")

        return elapsed_ms

    def _benchmark_encrypted_inserts(self, test_data: list[dict]) -> float:
        """Benchmark encrypted inserts using bulk_create."""
        # Prepare records with encryption
        persons = []
        prep_start = perf_counter()

        for record in test_data:
            persons.append(PersonEncrypted(
                first_name=record["first_name"],
                last_name=record["last_name"],
                email=record["email"],
                ssn_ciphertext=encrypt_ssn(record["ssn"]),
                ssn_hash=hash_ssn(record["ssn"]),
            ))

        prep_time = (perf_counter() - prep_start) * 1000

        # Insert
        insert_start = perf_counter()
        PersonEncrypted.objects.bulk_create(persons, batch_size=BATCH_SIZE)
        insert_time = (perf_counter() - insert_start) * 1000

        total_time = prep_time + insert_time

        self.stdout.write(f"  Preparation (crypto): {prep_time:.2f}ms")
        self.stdout.write(f"  DB insert: {insert_time:.2f}ms")
        self.stdout.write(f"  Total: {total_time:.2f}ms")

        # Cleanup
        PersonEncrypted.objects.filter(id__in=[p.id for p in persons]).delete()

        return total_time

    def _benchmark_baseline_inserts(self, test_data: list[dict]) -> float:
        """Benchmark baseline inserts using bulk_create."""
        # Prepare records without encryption
        persons = []
        prep_start = perf_counter()

        for record in test_data:
            persons.append(PersonBaseline(
                first_name=record["first_name"],
                last_name=record["last_name"],
                email=record["email"],
                ssn=normalize_ssn(record["ssn"]),
            ))

        prep_time = (perf_counter() - prep_start) * 1000

        # Insert
        insert_start = perf_counter()
        PersonBaseline.objects.bulk_create(persons, batch_size=BATCH_SIZE)
        insert_time = (perf_counter() - insert_start) * 1000

        total_time = prep_time + insert_time

        self.stdout.write(f"  Preparation: {prep_time:.2f}ms")
        self.stdout.write(f"  DB insert: {insert_time:.2f}ms")
        self.stdout.write(f"  Total: {total_time:.2f}ms")

        # Cleanup
        PersonBaseline.objects.filter(id__in=[p.id for p in persons]).delete()

        return total_time

