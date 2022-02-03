FROM python:3.10-alpine as builder

RUN apk add linux-headers gcc libc-dev
COPY requirements.txt /
WORKDIR /install
RUN pip install --prefix="/install" -r /requirements.txt 

FROM python:3.10-alpine

COPY --from=builder /install /usr/local
COPY evmqtt.py config.json /app/
WORKDIR /app

#COPY --from=builder /app .

CMD [ "python3", "./evmqtt.py"]
