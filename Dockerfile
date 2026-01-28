FROM python:3.10-alpine AS builder

RUN apk add --no-cache linux-headers gcc libc-dev

WORKDIR /build
COPY pyproject.toml requirements.txt ./
COPY src/ src/

RUN pip install --prefix="/install" .

FROM python:3.10-alpine

COPY --from=builder /install /usr/local
COPY config.json /app/

WORKDIR /app

CMD ["evmqtt", "-v"]
