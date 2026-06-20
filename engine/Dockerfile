FROM python:3.12-slim
WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Vendored free-llm-router Python twin (run `make vendor-router` before building).
COPY vendor/ /app/vendor/
ENV PYTHONPATH=/app/vendor

COPY groundcheck_engine/ /app/groundcheck_engine/

ENV GROUNDCHECK_ENGINE_HOST=0.0.0.0
EXPOSE 8723
CMD ["python", "-m", "groundcheck_engine"]
