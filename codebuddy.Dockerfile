FROM python:3.12-slim
WORKDIR /app
COPY vendor/codebuddy2api/requirements.txt ./requirements.txt
RUN pip install --no-cache-dir -r requirements.txt
COPY vendor/codebuddy2api/core ./core
COPY vendor/codebuddy2api/admin ./admin
CMD ["python", "-m", "admin.server"]
