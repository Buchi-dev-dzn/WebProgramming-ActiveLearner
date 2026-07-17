FROM python:3.12-alpine

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /sensor

COPY nids/monitor.py /sensor/monitor.py

CMD ["python", "/sensor/monitor.py"]
