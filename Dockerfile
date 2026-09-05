FROM python:3.12-slim

WORKDIR /app
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY bybit_recorder.py bybit_capture_audit.py l2_features.py bybit_protocol.json ./

# The container intentionally has no exchange credentials and no code path for
# authenticated order placement.  Runtime data is supplied as /data by Compose.
ENTRYPOINT ["python", "bybit_recorder.py"]
CMD ["--output", "/runtime/bybit_raw", "--rotate-minutes", "60"]
