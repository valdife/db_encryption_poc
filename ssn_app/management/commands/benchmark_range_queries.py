"""
Management command to benchmark range query performance with encrypted data.

This benchmark demonstrates the "decrypt-all" problem that occurs when you
encrypt a field that requires range queries (>, <, BETWEEN) or sorting.

Unlike SSN (equality queries only), income typically needs:
- Range filtering: WHERE income > 10000
- Sorting: ORDER BY income DESC
- These operations CANNOT use indexes on encrypted data

The benchmark compares:
1. ApplicantEncrypted: Must fetch ALL rows, decrypt in Python, filter, sort
2. ApplicantBaseline: PostgreSQL does filtering/sorting with indexes

This is the realistic scenario that the Fernet benchmark script demonstrates,
but using actual Django ORM and PostgreSQL instead of in-memory data.

Usage:
    python manage.py benchmark_range_queries
    python manage.py benchmark_range_queries --iterations 10
    python manage.py benchmark_range_queries --threshold 50000 --top-n 100
"""
import gc
import statistics
import tracemalloc
from dataclasses import dataclass, field
from decimal import Decimal
from time import perf_counter

from django.core.management.base import BaseCommand, CommandParser
from django.db import connection

from ssn_app.crypto import _get_fernet
from ssn_app.models import ApplicantBaseline, ApplicantEncrypted

ITERATIONS_DEFAULT = 5
THRESHOLD_DEFAULT = 10000
TOP_N_DEFAULT = 50


@dataclass
class RangeQueryResult:
    name: str
    iterations: int
    total_time_ms: float
    times_ms: list[float] = field(default_factory=list)
    memory_peak_mb: float = 0.0
    records_scanned: int = 0
    records_filtered: int = 0
    records_returned: int = 0

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
    def std_dev_ms(self) -> float:
        return statistics.stdev(self.times_ms) if len(self.times_ms) > 1 else 0


class Command(BaseCommand):
    help = "Benchmark range query performance with encrypted vs plain data"

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument(
            "--iterations",
            type=int,
            default=ITERATIONS_DEFAULT,
            help=f"Number of iterations per benchmark (default: {ITERATIONS_DEFAULT})",
        )
        parser.add_argument(
            "--threshold",
            type=int,
            default=THRESHOLD_DEFAULT,
            help=f"Income threshold for WHERE clause (default: {THRESHOLD_DEFAULT})",
        )
        parser.add_argument(
            "--top-n",
            type=int,
            default=TOP_N_DEFAULT,
            help=f"Number of top records to return (default: {TOP_N_DEFAULT})",
        )

    def handle(self, *args, **options) -> None:
        iterations = options["iterations"]
        threshold = Decimal(options["threshold"])
        top_n = options["top_n"]

        # Check data availability
        encrypted_count = ApplicantEncrypted.objects.count()
        baseline_count = ApplicantBaseline.objects.count()

        self.stdout.write("=" * 70)
        self.stdout.write("RANGE QUERY BENCHMARK: THE 'DECRYPT-ALL' PROBLEM")
        self.stdout.write("=" * 70)
        self.stdout.write("")
        self.stdout.write("Query: Get top N applicants WHERE income > threshold ORDER BY income DESC")
        self.stdout.write("")
        self.stdout.write(f"Configuration:")
        self.stdout.write(f"  - Income threshold: ${threshold:,}")
        self.stdout.write(f"  - Top N records: {top_n}")
        self.stdout.write(f"  - Iterations: {iterations}")
        self.stdout.write("")
        self.stdout.write(f"Dataset size:")
        self.stdout.write(f"  - ApplicantEncrypted: {encrypted_count:,} records")
        self.stdout.write(f"  - ApplicantBaseline:  {baseline_count:,} records")
        self.stdout.write("")

        if encrypted_count == 0 or baseline_count == 0:
            self.stdout.write(self.style.ERROR(
                "No applicant data found. Run the following first:\n"
                "  python manage.py generate_applicant_data --count 100000"
            ))
            return

        # Run benchmarks
        self.stdout.write("-" * 70)
        self.stdout.write("RUNNING BENCHMARKS")
        self.stdout.write("-" * 70)
        self.stdout.write("")

        # Baseline: PostgreSQL does the work
        baseline_result = self._benchmark_baseline(threshold, top_n, iterations)

        # Encrypted: Application must decrypt all
        encrypted_result = self._benchmark_encrypted(threshold, top_n, iterations)

        # Raw SQL baseline for reference
        sql_result = self._benchmark_raw_sql(threshold, top_n, iterations)

        # Print results
        self._print_results(baseline_result, encrypted_result, sql_result)

        # Print analysis
        self._print_analysis(baseline_result, encrypted_result, sql_result, encrypted_count)

    def _benchmark_baseline(
        self, threshold: Decimal, top_n: int, iterations: int
    ) -> RangeQueryResult:
        """
        Benchmark: PostgreSQL handles WHERE + ORDER BY + LIMIT.

        This is what the database is optimized for:
        - B-tree index on income enables O(log n) range scan
        - Sorting uses index order (no separate sort step)
        - LIMIT stops after N rows (doesn't fetch all)
        """
        self.stdout.write("  Baseline (PostgreSQL filtering/sorting)...")

        times_ms: list[float] = []
        records_returned = 0
        memory_peak = 0.0

        # Warmup
        list(ApplicantBaseline.objects.filter(income__gt=threshold).order_by("-income")[:top_n])

        for i in range(iterations):
            gc.collect()
            tracemalloc.start()

            start = perf_counter()

            # PostgreSQL does: WHERE income > threshold ORDER BY income DESC LIMIT top_n
            results = list(
                ApplicantBaseline.objects
                .filter(income__gt=threshold)
                .order_by("-income")[:top_n]
            )

            elapsed_ms = (perf_counter() - start) * 1000
            times_ms.append(elapsed_ms)
            records_returned = len(results)

            _, peak = tracemalloc.get_traced_memory()
            memory_peak = max(memory_peak, peak / (1024 * 1024))
            tracemalloc.stop()

        total_time = sum(times_ms)
        result = RangeQueryResult(
            name="Baseline (DB filtering)",
            iterations=iterations,
            total_time_ms=total_time,
            times_ms=times_ms,
            memory_peak_mb=memory_peak,
            records_scanned=top_n,  # DB only scans what it needs
            records_filtered=0,  # DB handles this
            records_returned=records_returned,
        )

        self.stdout.write(f"    -> avg: {result.avg_time_ms:.2f}ms, peak mem: {memory_peak:.2f}MB")
        return result

    def _benchmark_encrypted(
        self, threshold: Decimal, top_n: int, iterations: int
    ) -> RangeQueryResult:
        """
        Benchmark: Application must decrypt ALL records.

        This demonstrates the fundamental problem:
        1. Cannot use WHERE on ciphertext (random bytes)
        2. Cannot use ORDER BY on ciphertext (no order preservation)
        3. Must fetch ALL rows, decrypt ALL, filter, sort, then take top N

        Time complexity: O(n) where n = total records
        Memory: Must hold all decrypted values
        """
        self.stdout.write("  Encrypted (decrypt-all approach)...")

        times_ms: list[float] = []
        records_scanned = 0
        records_filtered = 0
        records_returned = 0
        memory_peak = 0.0

        fernet = _get_fernet()

        # Warmup
        gc.collect()

        for i in range(iterations):
            gc.collect()
            tracemalloc.start()

            start = perf_counter()

            # Step 1: Fetch ALL records from database
            # We use .only() to minimize data transfer, but still need all rows
            all_records = list(
                ApplicantEncrypted.objects
                .only("id", "name", "income_ciphertext")
                .iterator(chunk_size=2000)
            )
            records_scanned = len(all_records)

            # Step 2: Decrypt ALL income values
            decrypted = []
            for record in all_records:
                income_str = fernet.decrypt(record.income_ciphertext.encode()).decode()
                income = Decimal(income_str)
                decrypted.append({
                    "id": record.id,
                    "name": record.name,
                    "income": income,
                })

            # Step 3: Filter (WHERE income > threshold) - in Python
            filtered = [r for r in decrypted if r["income"] > threshold]
            records_filtered = len(filtered)

            # Step 4: Sort (ORDER BY income DESC) - in Python
            sorted_results = sorted(filtered, key=lambda x: x["income"], reverse=True)

            # Step 5: Take top N (LIMIT) - in Python
            top_results = sorted_results[:top_n]
            records_returned = len(top_results)

            elapsed_ms = (perf_counter() - start) * 1000
            times_ms.append(elapsed_ms)

            _, peak = tracemalloc.get_traced_memory()
            memory_peak = max(memory_peak, peak / (1024 * 1024))
            tracemalloc.stop()

        total_time = sum(times_ms)
        result = RangeQueryResult(
            name="Encrypted (decrypt-all)",
            iterations=iterations,
            total_time_ms=total_time,
            times_ms=times_ms,
            memory_peak_mb=memory_peak,
            records_scanned=records_scanned,
            records_filtered=records_filtered,
            records_returned=records_returned,
        )

        self.stdout.write(f"    -> avg: {result.avg_time_ms:.2f}ms, peak mem: {memory_peak:.2f}MB")
        return result

    def _benchmark_raw_sql(
        self, threshold: Decimal, top_n: int, iterations: int
    ) -> RangeQueryResult:
        """
        Benchmark: Raw SQL for absolute baseline.

        This shows the theoretical minimum - what PostgreSQL can achieve
        without any Django ORM overhead.
        """
        self.stdout.write("  Raw SQL (absolute baseline)...")

        times_ms: list[float] = []
        records_returned = 0

        sql = """
            SELECT id, name, income
            FROM applicant_baseline
            WHERE income > %s
            ORDER BY income DESC
            LIMIT %s
        """

        # Warmup
        with connection.cursor() as cursor:
            cursor.execute(sql, [threshold, top_n])
            cursor.fetchall()

        for i in range(iterations):
            start = perf_counter()

            with connection.cursor() as cursor:
                cursor.execute(sql, [threshold, top_n])
                results = cursor.fetchall()
                records_returned = len(results)

            elapsed_ms = (perf_counter() - start) * 1000
            times_ms.append(elapsed_ms)

        total_time = sum(times_ms)
        result = RangeQueryResult(
            name="Raw SQL (baseline)",
            iterations=iterations,
            total_time_ms=total_time,
            times_ms=times_ms,
            records_returned=records_returned,
        )

        self.stdout.write(f"    -> avg: {result.avg_time_ms:.2f}ms")
        return result

    def _print_results(
        self,
        baseline: RangeQueryResult,
        encrypted: RangeQueryResult,
        raw_sql: RangeQueryResult,
    ) -> None:
        self.stdout.write("")
        self.stdout.write("-" * 70)
        self.stdout.write("RESULTS")
        self.stdout.write("-" * 70)
        self.stdout.write("")

        header = f"{'Method':<30} {'Avg (ms)':>12} {'Min':>10} {'Max':>10} {'Memory':>10}"
        self.stdout.write(header)
        self.stdout.write("-" * len(header))

        for r in [raw_sql, baseline, encrypted]:
            mem_str = f"{r.memory_peak_mb:.1f}MB" if r.memory_peak_mb > 0 else "N/A"
            self.stdout.write(
                f"{r.name:<30} {r.avg_time_ms:>12.2f} {r.min_time_ms:>10.2f} "
                f"{r.max_time_ms:>10.2f} {mem_str:>10}"
            )

        self.stdout.write("")
        self.stdout.write(f"Records scanned (encrypted): {encrypted.records_scanned:,}")
        self.stdout.write(f"Records after filter:        {encrypted.records_filtered:,}")
        self.stdout.write(f"Records returned:            {encrypted.records_returned}")

    def _print_analysis(
        self,
        baseline: RangeQueryResult,
        encrypted: RangeQueryResult,
        raw_sql: RangeQueryResult,
        total_records: int,
    ) -> None:
        self.stdout.write("")
        self.stdout.write("-" * 70)
        self.stdout.write("ANALYSIS")
        self.stdout.write("-" * 70)
        self.stdout.write("")

        # Calculate overhead
        time_overhead = encrypted.avg_time_ms / baseline.avg_time_ms if baseline.avg_time_ms > 0 else float("inf")
        mem_overhead = encrypted.memory_peak_mb / baseline.memory_peak_mb if baseline.memory_peak_mb > 0 else float("inf")

        self.stdout.write("Performance Comparison:")
        self.stdout.write(f"  Time overhead:   {time_overhead:.1f}x slower with encryption")
        if encrypted.memory_peak_mb > 0 and baseline.memory_peak_mb > 0:
            self.stdout.write(f"  Memory overhead: {mem_overhead:.1f}x more memory with encryption")
        self.stdout.write("")

        self.stdout.write("Why is encrypted so much slower?")
        self.stdout.write("")
        self.stdout.write("  BASELINE (PostgreSQL):")
        self.stdout.write(f"    - Uses B-tree index on income column")
        self.stdout.write(f"    - WHERE income > X: Index seek O(log n)")
        self.stdout.write(f"    - ORDER BY income DESC: Index already sorted")
        self.stdout.write(f"    - LIMIT 50: Stop after 50 rows")
        self.stdout.write(f"    - Rows transferred: ~50")
        self.stdout.write("")
        self.stdout.write("  ENCRYPTED (Application):")
        self.stdout.write(f"    - Cannot use index on ciphertext (random bytes)")
        self.stdout.write(f"    - Must fetch ALL {total_records:,} rows")
        self.stdout.write(f"    - Must decrypt ALL {total_records:,} values (CPU-bound)")
        self.stdout.write(f"    - Filter in Python (interpreted, not C)")
        self.stdout.write(f"    - Sort in Python (timsort, but on {encrypted.records_filtered:,} items)")
        self.stdout.write(f"    - Finally take top 50")
        self.stdout.write("")

        # Verdict
        self.stdout.write("-" * 70)
        self.stdout.write("VERDICT")
        self.stdout.write("-" * 70)
        self.stdout.write("")

        threshold_ms = 200

        if encrypted.avg_time_ms < threshold_ms:
            self.stdout.write(f"  PASS: {encrypted.avg_time_ms:.0f}ms is under {threshold_ms}ms threshold")
            self.stdout.write("  However, this may not scale well with more data.")
        else:
            self.stdout.write(f"  FAIL: {encrypted.avg_time_ms:.0f}ms exceeds {threshold_ms}ms threshold")
            self.stdout.write("")
            self.stdout.write("  This demonstrates WHY you should NOT encrypt fields")
            self.stdout.write("  that require range queries or sorting.")
            self.stdout.write("")
            self.stdout.write("  SOLUTIONS:")
            self.stdout.write("    1. Don't encrypt income - use DB-level encryption instead")
            self.stdout.write("    2. Use ORDER-PRESERVING encryption (OPE) - weaker security")
            self.stdout.write("    3. Accept the performance cost for compliance")
            self.stdout.write("    4. Pre-compute ranges (buckets) and store hashes")
            self.stdout.write("")

        self.stdout.write("")
        self.stdout.write("KEY INSIGHT:")
        self.stdout.write("  SSN encryption (equality only) = O(log n) with hash index")
        self.stdout.write("  Income encryption (range/sort) = O(n) decrypt-all")
        self.stdout.write("")
        self.stdout.write("  Choose your encryption strategy based on query patterns!")

