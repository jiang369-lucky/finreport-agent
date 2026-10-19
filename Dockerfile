FROM python:3.11.9-slim-bookworm
WORKDIR /app
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1
COPY requirements.lock .
RUN python -m pip install --disable-pip-version-check pip==25.2 setuptools==80.9.0 wheel==0.45.1 \
    && python -m pip install --disable-pip-version-check --require-hashes --no-build-isolation -r requirements.lock
COPY finagent finagent
COPY scripts scripts
COPY workflows workflows
COPY web web
RUN mkdir -p data logs
EXPOSE 8765
CMD ["sh", "-c", "python -m scripts.init_database && exec uvicorn finagent.web:app --host 0.0.0.0 --port 8765"]
