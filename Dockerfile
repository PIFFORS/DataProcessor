FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY data-processor/ ./data-processor/
COPY data/ ./data/

CMD ["sh", "-c", "python data-processor/filter.py && python data-processor/fetch.py"]