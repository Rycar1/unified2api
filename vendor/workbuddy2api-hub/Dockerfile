# WorkBuddy Multi-Account Reverse Proxy Gateway
FROM python:3.11-alpine

# Set environment
ENV PYTHONUNBUFFERED=1     HOST=0.0.0.0     PORT=8788     API_KEY=     TZ=Asia/Shanghai

WORKDIR /app

# Alpine timezone & certs
RUN apk add --no-cache tzdata ca-certificates &&     cp /usr/share/zoneinfo/${TZ} /etc/localtime &&     echo "${TZ}" > /etc/timezone

# Copy application files (Zero external pip dependencies needed)
COPY wb_*.py dashboard.html LICENSE ./

# Create data directories
RUN mkdir -p /app/accounts /app/usage

# Volume persistence for credentials and usage logs
VOLUME ["/app/accounts", "/app/usage"]

EXPOSE 8788

# The service is reachable only on the Compose network. API_KEY is supplied
# through the environment; avoid --lan, which prints the key in startup logs.
CMD ["python", "wb_proxy.py", "--host", "0.0.0.0"]
