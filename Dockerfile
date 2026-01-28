# Build stage for Python package
ARG BUILD_FROM=ghcr.io/home-assistant/amd64-base-python:3.11
FROM python:3.11-alpine AS builder

RUN apk add --no-cache linux-headers gcc libc-dev

WORKDIR /build
COPY pyproject.toml requirements.txt ./
COPY src/ src/

RUN pip install --no-cache-dir --prefix="/install" .

# Final stage - Home Assistant Add-on
FROM ${BUILD_FROM}

# Install evmqtt package
COPY --from=builder /install /usr/local

# Copy add-on files
COPY run.sh /
RUN chmod a+x /run.sh

# Set working directory
WORKDIR /data

# Add labels for Home Assistant
LABEL \
    io.hass.name="evmqtt" \
    io.hass.description="Linux input event to MQTT gateway" \
    io.hass.type="addon" \
    io.hass.version="1.0.0"

CMD ["/run.sh"]
