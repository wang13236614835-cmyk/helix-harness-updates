FROM python:3.12-slim@sha256:a6e34c598f2467ed0e9a8d349809fcd8b5c603269512df273a0bb1784edc11b1
WORKDIR /app
COPY cloud/requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir -r /tmp/requirements.txt
COPY shell/server /app/shell/server
COPY shell/app /app/shell/app
COPY shell/launch /app/shell/launch
COPY cloud/helix.cloud.config.json /app/shell/helix.config.json
COPY cloud /app/cloud
RUN mkdir -p /app/shell/data /secrets && chown -R 1000:1000 /app/shell/data /secrets
USER 1000:1000
ENV PYTHONUTF8=1 PYTHONUNBUFFERED=1 HELIX_BIND=0.0.0.0 HELIX_PORT=18773 HELIX_SECRET_KEY_FILE=/secrets/encryption.key HELIX_RESEARCH_ONLY_BUNDLED=1
CMD ["python", "cloud/server.py"]
