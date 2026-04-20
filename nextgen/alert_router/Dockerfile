FROM python:3.12-slim

# SSH client required for mode-switch remote executor (Phase 2)
RUN apt-get update && apt-get install -y --no-install-recommends \
    openssh-client \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY app.py ./
COPY executor.py ./

CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8081"]
