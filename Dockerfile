FROM mcr.microsoft.com/playwright/python:v1.63.0-noble@sha256:72bd171a9ffc2b4b59532aaa6210e21014d07093120dc25528870c0b840da1f0

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# Patch base-image OS packages (Trivy gates on fixed HIGH/CRITICAL CVEs).
RUN apt-get update \
    && apt-get upgrade -y \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir --upgrade pip setuptools wheel \
    && pip install --no-cache-dir -r requirements.txt \
    && pip install --no-cache-dir --upgrade setuptools urllib3 \
    && pip uninstall -y virtualenv \
    && python -m playwright install chromium

COPY pricetracker ./pricetracker
COPY migrations ./migrations
COPY alembic.ini scrapy.cfg ./

RUN mkdir -p /app/data /app/exports \
    && chown -R pwuser:pwuser /app
USER pwuser

ENTRYPOINT ["python", "-m", "pricetracker"]
CMD ["report"]
