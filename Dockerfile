FROM python:3.12-slim

WORKDIR /app

# System deps for Playwright
RUN apt-get update && apt-get install -y \
    curl wget gnupg \
    && rm -rf /var/lib/apt/lists/*

# Python deps
COPY requirements.txt api/requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt -r api/requirements.txt
RUN pip install --no-cache-dir playwright
RUN playwright install chromium --with-deps

# Copy source
COPY . .
RUN pip install --no-cache-dir -e .

EXPOSE 8000

CMD ["uvicorn", "api.main:app", "--host", "0.0.0.0", "--port", "8000"]
