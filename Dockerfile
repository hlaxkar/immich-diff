FROM python:3.11-slim

WORKDIR /app

# Install package dependencies
COPY pyproject.toml README.md ./
COPY immich_comparator/ ./immich_comparator/

RUN pip install --no-cache-dir .

# Default command shows help; mount local media volume to /media
ENTRYPOINT ["immich-comparator"]
CMD ["--help"]
