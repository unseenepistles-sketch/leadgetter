FROM python:3.11-slim

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app
RUN mkdir -p /app/data

EXPOSE 8000
# One worker on purpose: the scheduler is in-process and the workbook has a single
# writer. Scale by putting a proxy in front, not by adding workers here.
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]
