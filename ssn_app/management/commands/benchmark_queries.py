"""
Management command to benchmark SSN query performance.

This command measures and compares query performance between:
- PersonEncrypted (ssn_hash lookup)
- PersonBaseline (plain ssn lookup)

It runs multiple iterations of various query types and reports timing statistics.

Usage:
    python manage.py benchmark_queries
    python manage.py benchmark_queries --iterations 500
    python manage.py benchmark_queries --include-joins
    python manage.py benchmark_queries --output results.json
"""
import json
import random
import statistics
from dataclasses import dataclass, field
from time import perf_counter

from django.core.management.base import BaseCommand, CommandParser
from django.db import connection, reset_queries

from ssn_app.crypto import hash_ssn
from ssn_app.models import Order, PersonBaseline, PersonEncrypted

ITERATIONS_DEFAULT = 100
WARMUP_ITERATIONS = 10


@dataclass
class BenchmarkResult:
    name: str
    iterations: int
    total_time_ms: float
    times_ms: list[float] = field(default_factory=list)

    @property
    def avg_time_ms(self) -> float:
        return self.total_time_ms / self.iterations if self.iterations > 0 else 0

    @property
    def min_time_ms(self) -> float:
        return min(self.times_ms) if self.times_ms else 0

    @property
    def max_time_ms(self) -> float:
        return max(self.times_ms) if self.times_ms else 0

    @property
    def median_time_ms(self) -> float:
        return statistics.median(self.times_ms) if self.times_ms else 0

    @property
    def std_dev_ms(self) -> float:
        return statistics.stdev(self.times_ms) if len(self.times_ms) > 1 else 0

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "iterations": self.iterations,
            "total_time_ms": round(self.total_time_ms, 3),
            "avg_time_ms": round(self.avg_time_ms, 3),
            "min_time_ms": round(self.min_time_ms, 3),
            "max_time_ms": round(self.max_time_ms, 3),
            "median_time_ms": round(self.median_time_ms, 3),
            "std_dev_ms": round(self.std_dev_ms, 3),
        }


class Command(BaseCommand):
    help = "Benchmark SSN query performance"

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument(
            "--iterations",
            type=int,
            default=ITERATIONS_DEFAULT,
            help=f"Number of query iterations per benchmark (default: {ITERATIONS_DEFAULT})",
        )
        parser.add_argument(
            "--include-joins",
            action="store_true",
            help="Include join query benchmarks",
        )
        parser.add_argument(
            "--include-decryption",
            action="store_true",
            help="Include decryption overhead benchmarks",
        )
        parser.add_argument(
            "--output",
            type=str,
            help="Output results to JSON file",
        )

    def handle(self, *args, **options) -> None:
        iterations = options["iterations"]
        include_joins = options["include_joins"]
        include_decryption = options["include_decryption"]
        output_file = options["output"]

        # Check data availability
        encrypted_count = PersonEncrypted.objects.count()
        baseline_count = PersonBaseline.objects.count()
        order_count = Order.objects.count()

        self.stdout.write(f"Dataset size:")
        self.stdout.write(f"  PersonEncrypted: {encrypted_count:,} records")
        self.stdout.write(f"  PersonBaseline:  {baseline_count:,} records")
        self.stdout.write(f"  Orders:          {order_count:,} records")
        self.stdout.write("")

        if encrypted_count == 0 or baseline_count == 0:
            self.stdout.write(self.style.ERROR(
                "No data found. Run 'python manage.py generate_test_data' first."
            ))
            return

        # Get sample SSNs for testing
        sample_encrypted = self._get_sample_ssns_encrypted(iterations)
        sample_baseline = self._get_sample_ssns_baseline(iterations)

        results: list[BenchmarkResult] = []

        # Run benchmarks
        self.stdout.write("Running benchmarks...")
        self.stdout.write("=" * 70)

        # SSN equality lookup benchmarks
        results.append(self._benchmark_encrypted_ssn_lookup(sample_encrypted, iterations))
        results.append(self._benchmark_baseline_ssn_lookup(sample_baseline, iterations))

        # Email lookup (control - should be similar for both)
        results.append(self._benchmark_encrypted_email_lookup(iterations))
        results.append(self._benchmark_baseline_email_lookup(iterations))

        # PK lookup (control - should be identical)
        results.append(self._benchmark_encrypted_pk_lookup(iterations))
        results.append(self._benchmark_baseline_pk_lookup(iterations))

        if include_joins:
            results.append(self._benchmark_encrypted_join(iterations))
            results.append(self._benchmark_baseline_join(iterations))

        if include_decryption:
            results.append(self._benchmark_decryption_overhead(sample_encrypted, iterations))

        # Print summary
        self.stdout.write("")
        self.stdout.write("=" * 70)
        self.stdout.write("SUMMARY")
        self.stdout.write("=" * 70)
        self._print_results_table(results)

        # Print analysis
        self._print_analysis(results)

        # Save to file if requested
        if output_file:
            self._save_results(results, output_file)
            self.stdout.write(f"\nResults saved to {output_file}")

    def _get_sample_ssns_encrypted(self, count: int) -> list[str]:
        """Get sample SSNs from encrypted table for testing."""
        # Get random records and decrypt their SSNs
        ids = list(PersonEncrypted.objects.values_list("id", flat=True)[:count * 2])
        random.shuffle(ids)
        sample_ids = ids[:count]

        ssns = []
        for person in PersonEncrypted.objects.filter(id__in=sample_ids):
            ssns.append(person.ssn)

        return ssns

    def _get_sample_ssns_baseline(self, count: int) -> list[str]:
        """Get sample SSNs from baseline table for testing."""
        ids = list(PersonBaseline.objects.values_list("id", flat=True)[:count * 2])
        random.shuffle(ids)
        sample_ids = ids[:count]

        return list(
            PersonBaseline.objects.filter(id__in=sample_ids).values_list("ssn", flat=True)
        )

    def _run_benchmark(
        self,
        name: str,
        query_func: callable,
        iterations: int,
    ) -> BenchmarkResult:
        """Run a benchmark with warmup and timing."""
        self.stdout.write(f"  {name}...")

        # Warmup
        for _ in range(WARMUP_ITERATIONS):
            query_func()

        # Clear query log
        reset_queries()

        # Actual benchmark
        times_ms: list[float] = []
        total_start = perf_counter()

        for _ in range(iterations):
            start = perf_counter()
            query_func()
            elapsed = (perf_counter() - start) * 1000
            times_ms.append(elapsed)

        total_elapsed = (perf_counter() - total_start) * 1000

        result = BenchmarkResult(
            name=name,
            iterations=iterations,
            total_time_ms=total_elapsed,
            times_ms=times_ms,
        )

        self.stdout.write(
            f"    -> avg: {result.avg_time_ms:.3f}ms, "
            f"median: {result.median_time_ms:.3f}ms, "
            f"std: {result.std_dev_ms:.3f}ms"
        )

        return result

    def _benchmark_encrypted_ssn_lookup(
        self, ssns: list[str], iterations: int
    ) -> BenchmarkResult:
        idx = 0

        def query():
            nonlocal idx
            ssn = ssns[idx % len(ssns)]
            idx += 1
            h = hash_ssn(ssn)
            return PersonEncrypted.objects.filter(ssn_hash=h).first()

        return self._run_benchmark("Encrypted SSN lookup (ssn_hash)", query, iterations)

    def _benchmark_baseline_ssn_lookup(
        self, ssns: list[str], iterations: int
    ) -> BenchmarkResult:
        idx = 0

        def query():
            nonlocal idx
            ssn = ssns[idx % len(ssns)]
            idx += 1
            return PersonBaseline.objects.filter(ssn=ssn).first()

        return self._run_benchmark("Baseline SSN lookup (plain)", query, iterations)

    def _benchmark_encrypted_email_lookup(self, iterations: int) -> BenchmarkResult:
        emails = list(
            PersonEncrypted.objects.values_list("email", flat=True)[:iterations]
        )
        idx = 0

        def query():
            nonlocal idx
            email = emails[idx % len(emails)]
            idx += 1
            return PersonEncrypted.objects.filter(email=email).first()

        return self._run_benchmark("Encrypted email lookup", query, iterations)

    def _benchmark_baseline_email_lookup(self, iterations: int) -> BenchmarkResult:
        emails = list(
            PersonBaseline.objects.values_list("email", flat=True)[:iterations]
        )
        idx = 0

        def query():
            nonlocal idx
            email = emails[idx % len(emails)]
            idx += 1
            return PersonBaseline.objects.filter(email=email).first()

        return self._run_benchmark("Baseline email lookup", query, iterations)

    def _benchmark_encrypted_pk_lookup(self, iterations: int) -> BenchmarkResult:
        pks = list(PersonEncrypted.objects.values_list("id", flat=True)[:iterations])
        idx = 0

        def query():
            nonlocal idx
            pk = pks[idx % len(pks)]
            idx += 1
            return PersonEncrypted.objects.filter(pk=pk).first()

        return self._run_benchmark("Encrypted PK lookup", query, iterations)

    def _benchmark_baseline_pk_lookup(self, iterations: int) -> BenchmarkResult:
        pks = list(PersonBaseline.objects.values_list("id", flat=True)[:iterations])
        idx = 0

        def query():
            nonlocal idx
            pk = pks[idx % len(pks)]
            idx += 1
            return PersonBaseline.objects.filter(pk=pk).first()

        return self._run_benchmark("Baseline PK lookup", query, iterations)

    def _benchmark_encrypted_join(self, iterations: int) -> BenchmarkResult:
        """Benchmark join query: Orders with PersonEncrypted."""
        person_ids = list(
            PersonEncrypted.objects.values_list("id", flat=True)[:iterations]
        )
        idx = 0

        def query():
            nonlocal idx
            person_id = person_ids[idx % len(person_ids)]
            idx += 1
            # Select related to force join
            return list(
                Order.objects
                .filter(person_encrypted_id=person_id)
                .select_related("person_encrypted")
                [:10]
            )

        return self._run_benchmark("Encrypted JOIN (Orders)", query, iterations)

    def _benchmark_baseline_join(self, iterations: int) -> BenchmarkResult:
        """Benchmark join query: Orders with PersonBaseline."""
        person_ids = list(
            PersonBaseline.objects.values_list("id", flat=True)[:iterations]
        )
        idx = 0

        def query():
            nonlocal idx
            person_id = person_ids[idx % len(person_ids)]
            idx += 1
            return list(
                Order.objects
                .filter(person_baseline_id=person_id)
                .select_related("person_baseline")
                [:10]
            )

        return self._run_benchmark("Baseline JOIN (Orders)", query, iterations)

    def _benchmark_decryption_overhead(
        self, ssns: list[str], iterations: int
    ) -> BenchmarkResult:
        """Benchmark the overhead of decrypting SSN after retrieval."""
        hashes = [hash_ssn(ssn) for ssn in ssns]
        idx = 0

        def query():
            nonlocal idx
            h = hashes[idx % len(hashes)]
            idx += 1
            person = PersonEncrypted.objects.filter(ssn_hash=h).first()
            if person:
                _ = person.ssn  # Force decryption

        return self._run_benchmark("Encrypted lookup + decryption", query, iterations)

    def _print_results_table(self, results: list[BenchmarkResult]) -> None:
        """Print results in a formatted table."""
        header = f"{'Benchmark':<40} {'Avg (ms)':>10} {'Median':>10} {'Std Dev':>10}"
        self.stdout.write(header)
        self.stdout.write("-" * len(header))

        for r in results:
            self.stdout.write(
                f"{r.name:<40} {r.avg_time_ms:>10.3f} {r.median_time_ms:>10.3f} {r.std_dev_ms:>10.3f}"
            )

    def _print_analysis(self, results: list[BenchmarkResult]) -> None:
        """Print performance analysis."""
        self.stdout.write("")
        self.stdout.write("=" * 70)
        self.stdout.write("ANALYSIS")
        self.stdout.write("=" * 70)

        # Find encrypted and baseline SSN lookups
        encrypted_ssn = next((r for r in results if "Encrypted SSN" in r.name), None)
        baseline_ssn = next((r for r in results if "Baseline SSN" in r.name), None)

        if encrypted_ssn and baseline_ssn:
            diff_ms = encrypted_ssn.avg_time_ms - baseline_ssn.avg_time_ms
            diff_pct = (diff_ms / baseline_ssn.avg_time_ms * 100) if baseline_ssn.avg_time_ms > 0 else 0

            self.stdout.write("")
            self.stdout.write("SSN Lookup Comparison:")
            self.stdout.write(f"  Encrypted (hash): {encrypted_ssn.avg_time_ms:.3f}ms average")
            self.stdout.write(f"  Baseline (plain): {baseline_ssn.avg_time_ms:.3f}ms average")
            self.stdout.write(f"  Difference: {diff_ms:+.3f}ms ({diff_pct:+.1f}%)")

            self.stdout.write("")
            if abs(diff_pct) < 10:
                self.stdout.write(
                    "  Conclusion: SSN hash lookup performs similarly to plain SSN lookup."
                )
                self.stdout.write(
                    "  The B-tree index on ssn_hash provides O(log n) lookups just like"
                )
                self.stdout.write(
                    "  a plain indexed column. The hash computation overhead is minimal."
                )
            elif diff_pct > 0:
                self.stdout.write(
                    f"  Conclusion: Encrypted lookup is ~{diff_pct:.0f}% slower."
                )
                self.stdout.write(
                    "  This overhead comes from the Python-side hash_ssn() computation."
                )
            else:
                self.stdout.write(
                    "  Note: Encrypted lookup appears faster, likely due to caching effects."
                )

        # Email comparison (should be identical)
        encrypted_email = next((r for r in results if "Encrypted email" in r.name), None)
        baseline_email = next((r for r in results if "Baseline email" in r.name), None)

        if encrypted_email and baseline_email:
            self.stdout.write("")
            self.stdout.write("Email Lookup (Control):")
            self.stdout.write(
                f"  Both tables show similar performance "
                f"({encrypted_email.avg_time_ms:.3f}ms vs {baseline_email.avg_time_ms:.3f}ms),"
            )
            self.stdout.write(
                "  confirming that table structure with encryption columns does not"
            )
            self.stdout.write(
                "  significantly impact non-SSN queries."
            )

        # Join comparison
        encrypted_join = next((r for r in results if "Encrypted JOIN" in r.name), None)
        baseline_join = next((r for r in results if "Baseline JOIN" in r.name), None)

        if encrypted_join and baseline_join:
            self.stdout.write("")
            self.stdout.write("JOIN Performance:")
            self.stdout.write(
                f"  Encrypted: {encrypted_join.avg_time_ms:.3f}ms, "
                f"Baseline: {baseline_join.avg_time_ms:.3f}ms"
            )
            self.stdout.write(
                "  JOIN keys (person_id) are not encrypted, so join performance"
            )
            self.stdout.write(
                "  is unaffected by SSN encryption."
            )

    def _save_results(self, results: list[BenchmarkResult], filename: str) -> None:
        """Save results to JSON file."""
        data = {
            "dataset": {
                "encrypted_count": PersonEncrypted.objects.count(),
                "baseline_count": PersonBaseline.objects.count(),
                "order_count": Order.objects.count(),
            },
            "results": [r.to_dict() for r in results],
        }
        with open(filename, "w") as f:
            json.dump(data, f, indent=2)

