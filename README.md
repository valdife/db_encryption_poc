# SSN Column-Level Encryption PoC

A Proof of Concept demonstrating **application-level column encryption** for Social Security Numbers (SSN) in Django with PostgreSQL, including performance benchmarking for large datasets.

## Table of Contents

1. [Overview](#overview)
2. [Security Design](#security-design)
3. [Model Design](#model-design)
4. [Crypto Implementation](#crypto-implementation)
5. [Usage Examples](#usage-examples)
6. [Performance Testing](#performance-testing)
7. [The "Decrypt-All" Problem](#the-decrypt-all-problem)
8. [Results and Conclusions](#results-and-conclusions)
9. [Limitations and Caveats](#limitations-and-caveats)
10. [Quick Start](#quick-start)

---

## Overview

### Goal

Implement secure storage of SSN in Django with:

1. **Application-level encryption** using Fernet (AES-128-CBC + HMAC-SHA256)
2. **Hashed SSN column** for efficient equality queries
3. **Performance benchmarks** comparing encrypted vs. plain storage

### Architecture

```
+------------------+       +------------------+       +------------------+
|   Application    |       |     Django       |       |   PostgreSQL     |
|                  |       |                  |       |                  |
|  user provides   | ----> | normalize()      | ----> | ssn_ciphertext   |
|  SSN: 123-45-6789|       | encrypt()        |       | (encrypted blob) |
|                  |       | hash()           |       |                  |
|                  |       |                  |       | ssn_hash         |
|                  |       |                  |       | (indexed, 64 chr)|
+------------------+       +------------------+       +------------------+
```

### What This Protects Against

- Database dumps and backups exposing plaintext SSNs
- DBA or unauthorized DB access seeing raw SSN values
- SQL injection attacks that exfiltrate data
- Stolen database files

### What This Does NOT Protect Against

- Compromised application server with access to encryption keys
- Memory dumps from running application
- Authorized application access (the app can always decrypt)
- Side-channel attacks on the application

---

## Security Design

### SSN Processing Pipeline

```
Raw SSN Input          Normalize           Encrypt              Store
"123-45-6789"   --->   "123456789"   --->  "gAAAAAB..."   --->  ssn_ciphertext
                            |
                            v
                        Hash (SHA-256)
                            |
                            v
                    "a1b2c3d4..."   --->  ssn_hash (indexed)
```

### 1. Normalization

Before any cryptographic operation, SSNs are normalized to ensure consistent processing:

```python
def normalize_ssn(ssn: str) -> str:
    # "123-45-6789" -> "123456789"
    # "123 45 6789" -> "123456789"
    normalized = re.sub(r"[^0-9]", "", ssn)
    if len(normalized) != 9:
        raise ValueError("Invalid SSN")
    return normalized
```

### 2. Encryption Scheme

**Algorithm**: Fernet (from `cryptography` library)
- AES-128-CBC for encryption
- HMAC-SHA256 for authentication
- Timestamp for optional TTL validation
- Base64 encoding for storage

**Properties**:
- Authenticated encryption (tamper-evident)
- Same plaintext produces different ciphertext each time (random IV)
- ~120 bytes ciphertext for 9-digit SSN

```python
from cryptography.fernet import Fernet

def encrypt_ssn(ssn: str) -> str:
    normalized = normalize_ssn(ssn)
    fernet = Fernet(settings.SSN_ENCRYPTION_KEY)
    return fernet.encrypt(normalized.encode()).decode()

def decrypt_ssn(ciphertext: str) -> str:
    fernet = Fernet(settings.SSN_ENCRYPTION_KEY)
    return fernet.decrypt(ciphertext.encode()).decode()
```

### 3. Hashing for Equality Queries

Since encrypted values cannot be compared (each encryption produces different output), we store a deterministic hash for lookups:

```python
def hash_ssn(ssn: str) -> str:
    normalized = normalize_ssn(ssn)
    salt = settings.SSN_HASH_SALT
    salted = f"{salt}{normalized}"
    return hashlib.sha256(salted.encode()).hexdigest()
```

**Why salted hash?**
- Prevents rainbow table attacks
- Makes hash values unique to your application
- Without salt: attacker with DB access could precompute all 10^9 SSN hashes

**Security Note**: The SSN space is small (10^9 values). A determined attacker with the salt could still brute-force hashes. The hash is for query efficiency, not security. Access controls remain essential.

### 4. Key Management

Keys and salts are read from environment variables:

```bash
# Generate encryption key (Fernet requires specific format)
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"

# Generate hash salt (random hex string)
python -c "import secrets; print(secrets.token_hex(32))"
```

**Environment Variables**:
```bash
export SSN_ENCRYPTION_KEY="your-fernet-key-here"
export SSN_HASH_SALT="your-random-salt-here"
```

---

## Model Design

### Encrypted Model

```python
class PersonEncrypted(models.Model):
    first_name = models.CharField(max_length=100)
    last_name = models.CharField(max_length=100)
    email = models.EmailField(db_index=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    # Encrypted SSN storage
    ssn_ciphertext = models.TextField()
    ssn_hash = models.CharField(max_length=64, db_index=True)

    @property
    def ssn(self) -> str | None:
        """Decrypt and return the SSN."""
        if not self.ssn_ciphertext:
            return None
        return decrypt_ssn(self.ssn_ciphertext)

    @ssn.setter
    def ssn(self, value: str) -> None:
        """Encrypt the SSN and compute its hash."""
        self.ssn_ciphertext = encrypt_ssn(value)
        self.ssn_hash = hash_ssn(value)

    @property
    def ssn_masked(self) -> str:
        """Return masked SSN for display (***-**-6789)."""
        return mask_ssn(self.ssn)
```

### Database Schema

```sql
CREATE TABLE person_encrypted (
    id BIGSERIAL PRIMARY KEY,
    first_name VARCHAR(100) NOT NULL,
    last_name VARCHAR(100) NOT NULL,
    email VARCHAR(254) NOT NULL,
    created_at TIMESTAMP WITH TIME ZONE NOT NULL,
    updated_at TIMESTAMP WITH TIME ZONE NOT NULL,
    ssn_ciphertext TEXT NOT NULL,
    ssn_hash VARCHAR(64) NOT NULL
);

CREATE INDEX idx_person_encrypted_ssn_hash ON person_encrypted(ssn_hash);
CREATE INDEX idx_person_encrypted_email ON person_encrypted(email);
```

### Baseline Model (for benchmarking only)

```python
class PersonBaseline(models.Model):
    # Same fields...
    ssn = models.CharField(max_length=9, db_index=True)  # Plain text!
```

---

## Crypto Implementation

The complete crypto module (`ssn_app/crypto.py`) provides:

| Function | Purpose |
|----------|---------|
| `normalize_ssn(ssn)` | Strip non-digits, validate 9 digits |
| `encrypt_ssn(ssn)` | Normalize + Fernet encrypt |
| `decrypt_ssn(ciphertext)` | Fernet decrypt |
| `hash_ssn(ssn)` | Normalize + salted SHA-256 |
| `mask_ssn(ssn)` | Return `***-**-XXXX` format |

### Exception Hierarchy

```python
SSNCryptoError          # Base exception
  SSNEncryptionError    # Encryption failed
  SSNDecryptionError    # Decryption failed (wrong key, corrupted data)
  SSNConfigurationError # Missing key or salt
```

---

## Usage Examples

### Creating a Record

```python
from ssn_app.models import PersonEncrypted

# Using the property (recommended)
person = PersonEncrypted(
    first_name="John",
    last_name="Doe",
    email="john.doe@example.com"
)
person.ssn = "123-45-6789"  # Automatically encrypts and hashes
person.save()
```

### Querying by SSN

```python
from ssn_app.models import PersonEncrypted
from ssn_app.crypto import hash_ssn

# Compute hash and query
ssn_to_find = "123-45-6789"
person = PersonEncrypted.objects.filter(
    ssn_hash=hash_ssn(ssn_to_find)
).first()

if person:
    print(f"Found: {person.first_name} {person.last_name}")
    print(f"SSN: {person.ssn}")  # Decrypts automatically
    print(f"Masked: {person.ssn_masked}")  # ***-**-6789
```

### Bulk Operations

For bulk inserts, pre-compute encryption/hashes:

```python
from ssn_app.crypto import encrypt_ssn, hash_ssn

persons = []
for data in bulk_data:
    persons.append(PersonEncrypted(
        first_name=data["first_name"],
        last_name=data["last_name"],
        email=data["email"],
        ssn_ciphertext=encrypt_ssn(data["ssn"]),
        ssn_hash=hash_ssn(data["ssn"]),
    ))

PersonEncrypted.objects.bulk_create(persons, batch_size=1000)
```

### Admin Integration

The admin is configured to:
- Never display `ssn_ciphertext` directly
- Show only masked SSN in list views
- Make SSN fields read-only

---

## Performance Testing

### Test Setup

Generate test data:

```bash
# Generate 100k records for both models
python manage.py generate_test_data --count 100000 --clear

# Generate 1M records (may take several minutes)
python manage.py generate_test_data --count 1000000 --clear

# Include orders for join testing
python manage.py generate_test_data --count 100000 --with-orders --orders-per-person 3
```

### Benchmarking Commands

**Query Performance**:
```bash
python manage.py benchmark_queries --iterations 500
python manage.py benchmark_queries --iterations 500 --include-joins --include-decryption
python manage.py benchmark_queries --output results.json
```

**Insert Performance**:
```bash
python manage.py benchmark_inserts --count 5000
python manage.py benchmark_inserts --count 10000 --output insert_results.json
```

### What Gets Measured

| Benchmark | Description |
|-----------|-------------|
| Encrypted SSN lookup | `filter(ssn_hash=hash_ssn(...))` |
| Baseline SSN lookup | `filter(ssn=...)` |
| Email lookup (control) | Should be identical for both |
| PK lookup (control) | Should be identical for both |
| JOIN queries | Orders -> Person with select_related |
| Insert overhead | Time for encryption vs. plain inserts |
| Range queries (income) | Demonstrates "decrypt-all" problem |

---

## The "Decrypt-All" Problem

### When Encryption Strategy Matters

The SSN encryption design works well because SSN queries are **equality-only**:
- "Find person with SSN 123-45-6789" - Use hash index, O(log n)

But what about fields that need **range queries or sorting**?
- "Get top 50 applicants with income > $10,000 sorted by income"
- This CANNOT use indexes on encrypted data

### The Problem Illustrated

```
QUERY: SELECT * FROM applicants WHERE income > 10000 ORDER BY income DESC LIMIT 50

BASELINE (Plain income with index):
  1. B-tree index seek to find income > 10000: O(log n)
  2. Index already sorted by income: no extra sort
  3. LIMIT 50: stop after 50 rows
  4. Rows transferred: ~50
  5. Time: ~1-5ms

ENCRYPTED (Ciphertext cannot be indexed meaningfully):
  1. Fetch ALL 100,000 rows from database
  2. Decrypt ALL 100,000 income values in Python
  3. Filter in Python: income > 10000
  4. Sort in Python: ORDER BY income DESC
  5. Take top 50
  6. Time: ~2000-5000ms (1000x slower!)
```

### Benchmark: Income Encryption

We include `ApplicantEncrypted` and `ApplicantBaseline` models to demonstrate this:

```bash
# Generate 100k applicants with income data
python manage.py generate_applicant_data --count 100000

# Run the decrypt-all benchmark
python manage.py benchmark_range_queries --threshold 10000 --top-n 50
```

Expected output:
```
Method                         Avg (ms)        Min        Max     Memory
---------------------------------------------------------------------------
Raw SQL (baseline)                 2.50       2.10       3.20        N/A
Baseline (DB filtering)            5.80       4.50       7.20      0.5MB
Encrypted (decrypt-all)         2450.00    2300.00    2600.00     85.0MB

Performance Comparison:
  Time overhead:   422x slower with encryption
  Memory overhead: 170x more memory with encryption
```

### Methodology Notes

This benchmark represents **realistic Django/PostgreSQL performance**, including:

1. **Actual database I/O** - Not in-memory data
2. **Django ORM overhead** - Real model instantiation
3. **PostgreSQL query planning** - Actual query execution
4. **Network latency** - Local PostgreSQL, but still socket communication

Real-world production performance may be **even slower** due to:
- Network latency to remote database
- Concurrent query contention
- Cold cache scenarios
- Per-tenant encryption keys

### When to Use Each Strategy

| Field Type | Query Pattern | Strategy | Performance |
|------------|---------------|----------|-------------|
| SSN | Equality only | Hash + Encrypt | O(log n) |
| Income | Range + Sort | **Don't encrypt at app level** | - |
| Credit Card | Equality only | Hash + Encrypt | O(log n) |
| Salary | Aggregation (SUM, AVG) | **Don't encrypt at app level** | - |
| Name | Search/Sort | **Don't encrypt at app level** | - |

### Alternatives for Range-Query Fields

If you must protect fields that need range queries:

1. **Database-level encryption (TDE)**: PostgreSQL encrypts at disk level, queries work normally
2. **Order-Preserving Encryption (OPE)**: Weaker security, but enables range queries
3. **Bucketing**: Store income range (e.g., "50k-75k") as hash, accept imprecision
4. **Separate reporting database**: Decrypt to analytics DB with restricted access
5. **Accept the cost**: For compliance, sometimes 2+ seconds is acceptable

---

## Results and Conclusions

### Expected Performance Characteristics

#### Read Queries (SSN Lookup)

| Metric | Encrypted | Baseline | Difference |
|--------|-----------|----------|------------|
| Index type | B-tree on `ssn_hash` | B-tree on `ssn` | Same |
| Index size | 64 bytes/row | 9 bytes/row | ~7x larger |
| Lookup complexity | O(log n) | O(log n) | Same |
| Python overhead | `hash_ssn()` call | None | ~0.01-0.05ms |

**Key insight**: The database query time is virtually identical because both use B-tree indexes. The only overhead is the Python-side `hash_ssn()` computation, which takes microseconds.

#### Write Operations (Insert/Update)

| Operation | Encrypted | Baseline | Overhead |
|-----------|-----------|----------|----------|
| Encryption | ~0.05-0.1ms/record | N/A | 100% of crypto time |
| Hashing | ~0.01ms/record | N/A | 100% of crypto time |
| DB insert | ~0.5-2ms/record | ~0.5-2ms/record | Same |

**Key insight**: Encryption overhead is typically 5-20% of total insert time, with most time spent on database I/O.

#### Join Performance

JOIN queries are **unaffected** by SSN encryption because:
- Join keys (foreign keys) are not encrypted
- The encrypted columns are only read after the join completes

### Scaling Behavior

| Table Size | Encrypted Lookup | Baseline Lookup | Notes |
|------------|------------------|-----------------|-------|
| 100k rows | ~0.5ms | ~0.5ms | Index fully in memory |
| 1M rows | ~0.6ms | ~0.6ms | Slight increase |
| 10M rows | ~0.8ms | ~0.8ms | Index depth increases |

Both scale logarithmically with table size due to B-tree indexing.

### Index Size Impact

```sql
-- Check index sizes
SELECT
    indexrelname,
    pg_size_pretty(pg_relation_size(indexrelid)) as size
FROM pg_stat_user_indexes
WHERE schemaname = 'public';
```

The `ssn_hash` index (64-byte values) will be approximately 7x larger than a plain `ssn` index (9 bytes). For 1M rows:
- Plain SSN index: ~30-50 MB
- Hash index: ~100-150 MB

This is acceptable for most use cases but worth monitoring for very large tables.

### Interpretation Template

After running benchmarks, interpret results like this:

> "Inserting 100,000 records:
> - Without encryption: 45 seconds (2,222 records/sec)
> - With encryption: 52 seconds (1,923 records/sec)
> - Overhead: 7 seconds (15.5%)
>
> This overhead is acceptable for our expected write volume of ~1000 records/hour.
>
> Read queries by SSN via `ssn_hash` showed 0.52ms average (encrypted) vs. 0.48ms average (baseline), a difference of 8% that is within measurement noise. User-facing latency will be dominated by network and application logic, not encryption."

---

## Limitations and Caveats

### Functional Limitations

1. **No sorting by SSN**: Encrypted/hashed values cannot be sorted meaningfully
2. **No range queries**: Cannot do `WHERE ssn > '500-00-0000'`
3. **No prefix/partial matching**: Cannot do `WHERE ssn LIKE '123-%'`
4. **No full-text search**: Encryption prevents any text analysis

These limitations are **inherent to proper encryption** and typically acceptable for SSN use cases.

### Security Caveats

1. **Key compromise**: If `SSN_ENCRYPTION_KEY` is compromised, all SSNs can be decrypted
2. **Salt compromise**: If `SSN_HASH_SALT` is compromised with the hashes, SSNs can be brute-forced (~10^9 attempts)
3. **Application-level access**: Any code with DB access and the key can decrypt SSNs
4. **Logging**: Ensure plaintext SSNs are **never logged**:

```python
# BAD - logs plaintext SSN
logger.info(f"Processing person with SSN {person.ssn}")

# GOOD - logs masked SSN
logger.info(f"Processing person with SSN {person.ssn_masked}")

# BETTER - don't log SSN at all
logger.info(f"Processing person {person.id}")
```

### Key Rotation (Not Implemented)

For production, implement key rotation:

1. Add `encryption_key_version` column to track which key encrypted each record
2. Support multiple active keys (old for reading, new for writing)
3. Create migration command to re-encrypt records with new key
4. Rotate keys periodically and after any suspected compromise

```python
# Conceptual key rotation
class PersonEncrypted(models.Model):
    ssn_ciphertext = models.TextField()
    ssn_hash = models.CharField(max_length=64, db_index=True)
    encryption_key_version = models.IntegerField(default=1)  # Add this

def decrypt_ssn(ciphertext: str, key_version: int) -> str:
    key = get_key_for_version(key_version)
    fernet = Fernet(key)
    return fernet.decrypt(ciphertext.encode()).decode()
```

### Compliance Notes

- **HIPAA, PCI-DSS, GDPR**: Encryption at rest is often required
- **Audit logging**: Log access to SSN (who viewed/modified, when)
- **Data retention**: Encrypted data still counts as PII for retention policies
- **Right to erasure**: Deleting record removes both ciphertext and hash

---

## Quick Start

### 1. Start PostgreSQL with Docker

```bash
# Start PostgreSQL container
docker compose up -d

# Verify it's running
docker compose ps
```

### 2. Install Dependencies

```bash
# Using uv (recommended)
uv sync

# Or using pip
pip install -e .
```

### 3. Set Environment Variables

```bash
# Generate keys
export SSN_ENCRYPTION_KEY=$(python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())")
export SSN_HASH_SALT=$(python -c "import secrets; print(secrets.token_hex(32))")

# Database (matches docker-compose.yml defaults)
export DB_NAME=ssn_encryption_poc
export DB_USER=postgres
export DB_PASSWORD=postgres
export DB_HOST=localhost
export DB_PORT=5432
```

### 4. Run Migrations

```bash
python manage.py migrate
```

### 5. Generate Test Data

```bash
# SSN benchmark data (100k records)
python manage.py generate_test_data --count 100000 --clear

# Income benchmark data (100k applicants)
python manage.py generate_applicant_data --count 100000 --clear
```

### 6. Run Benchmarks

```bash
# SSN equality query benchmarks (should be fast)
python manage.py benchmark_queries --iterations 200

# Insert performance benchmarks
python manage.py benchmark_inserts --count 5000

# Range query benchmarks - demonstrates the "decrypt-all" problem
python manage.py benchmark_range_queries --iterations 5
```

### 7. Explore in Shell

```bash
python manage.py shell
```

```python
from ssn_app.models import PersonEncrypted
from ssn_app.crypto import hash_ssn

# Create
p = PersonEncrypted(first_name="Jane", last_name="Smith", email="jane@example.com")
p.ssn = "987-65-4321"
p.save()

# Query
found = PersonEncrypted.objects.filter(ssn_hash=hash_ssn("987-65-4321")).first()
print(found.ssn)  # "987654321"
print(found.ssn_masked)  # "***-**-4321"
```

### 8. Cleanup

```bash
# Stop PostgreSQL
docker compose down

# Stop and remove data
docker compose down -v
```

---

## File Structure

```
db_encryption_poc/
|-- config/
|   |-- __init__.py
|   |-- settings.py              # Django settings with SSN config
|   |-- urls.py
|   |-- wsgi.py
|-- ssn_app/
|   |-- __init__.py
|   |-- admin.py                 # Admin with masked SSN display
|   |-- crypto.py                # Encryption/hashing utilities
|   |-- models.py                # All models (Person*, Applicant*, Order)
|   |-- management/
|       |-- commands/
|           |-- generate_test_data.py       # Generate Person data
|           |-- generate_applicant_data.py  # Generate Applicant data
|           |-- benchmark_queries.py        # SSN equality benchmarks
|           |-- benchmark_inserts.py        # Insert overhead benchmarks
|           |-- benchmark_range_queries.py  # Decrypt-all problem demo
|-- docker/
|   |-- postgres.conf            # PostgreSQL tuning for benchmarks
|-- docker-compose.yml           # PostgreSQL container setup
|-- manage.py
|-- pyproject.toml
|-- README.md
```

---

## Tech Stack

- **Python**: 3.13+
- **Django**: 5.1+ (latest LTS)
- **PostgreSQL**: 14+ (any recent version)
- **cryptography**: 43+ (Fernet encryption)
- **Faker**: 30+ (test data generation)
- **psycopg**: 3.2+ (PostgreSQL adapter)

