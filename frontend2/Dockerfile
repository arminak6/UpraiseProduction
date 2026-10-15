FROM python:3.12.14-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    FRONTEND2_HOST=0.0.0.0

WORKDIR /app
COPY serve.py index.html styles.css api.js ui.js app.js favicon.svg ./
EXPOSE 8502
CMD ["python", "serve.py"]
