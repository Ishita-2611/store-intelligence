FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends libgl1 libglib2.0-0 \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt requirements-ml.txt .
ARG INSTALL_GPU=false
RUN if [ "$INSTALL_GPU" = "true" ]; then \
        pip install --no-cache-dir -r requirements-ml.txt; \
    else \
        pip install --no-cache-dir --extra-index-url https://download.pytorch.org/whl/cpu -r requirements-ml.txt; \
    fi

COPY app ./app
COPY pipeline ./pipeline
COPY data ./data
COPY README.md .

RUN mkdir -p outputs/uploads

EXPOSE 8000

CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000}"]
