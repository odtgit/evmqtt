FROM python:3.7-alpine

COPY evmqtt.py config.json requirements.txt /app/
WORKDIR /app

RUN apk add --no-cache linux-headers gcc libc-dev tzdata

RUN pip --no-cache-dir --trusted-host pypi.org install --upgrade -r requirements.txt \
  && rm requirements.txt 

CMD [ "python", "./evmqtt.py"]
