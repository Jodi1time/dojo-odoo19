FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app
COPY tools/sync_whatsmax_hub.py tools/private_env.py ./
USER 10001:10001
ENTRYPOINT ["python", "/app/sync_whatsmax_hub.py"]
