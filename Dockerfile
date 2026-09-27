# Build stage for Python package
ARG BUILD_FROM=ghcr.io/home-assistant/amd64-base-python:3.13-alpine3.24
FROM python:3.13-alpine3.24 AS builder

RUN apk add --no-cache linux-headers gcc libc-dev

WORKDIR /build
COPY pyproject.toml ./
COPY src/ src/

RUN pip install --no-cache-dir --prefix="/install" ".[mqtt]"

# Final stage - Home Assistant Add-on or standalone
FROM ${BUILD_FROM}

# Install evmqtt package
COPY --from=builder /install /usr/local

# Copy entrypoint
COPY run.sh /
RUN chmod a+x /run.sh

# Set working directory
WORKDIR /data

# Add labels for Home Assistant
LABEL \
    io.hass.name="evmqtt" \
    io.hass.description="Linux input event to MQTT gateway" \
    io.hass.type="addon" \
    io.hass.version="1.1.0"

CMD ["/run.sh"]
