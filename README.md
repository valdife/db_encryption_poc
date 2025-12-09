# SSN Column-Level Encryption PoC

A Proof of Concept demonstrating **application-level column encryption** for Social Security Numbers (SSN) in Django with PostgreSQL, including performance benchmarking for large datasets.

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
cp .env.sample .env
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

