FROM python:3.12-alpine

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /sensor

RUN addgroup -S -g 1000 sensor && adduser -S -D -u 1000 -G sensor sensor

COPY hids/monitor.py /sensor/monitor.py

USER sensor

CMD ["python", "/sensor/monitor.py"]
