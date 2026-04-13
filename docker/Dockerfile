FROM python:3.12-slim

# SWI-Prolog install
RUN apt-get update && \
    apt-get install -y --no-install-recommends swi-prolog && \
    apt-get clean && rm -rf /var/lib/apt/lists/*

# Verify SWI-Prolog
RUN swipl --version

WORKDIR /app

# Copy project files
COPY pyproject.toml README.md LICENSE ./
COPY src/ src/

# Install package with all extras
RUN pip install --no-cache-dir ".[all,dev]"

# Copy tests, examples, benchmarks
COPY tests/ tests/
COPY examples/ examples/
COPY benchmarks/ benchmarks/

CMD ["pytest", "tests/", "-v"]
