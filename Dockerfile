FROM python:3.12-slim

WORKDIR /app

ARG BUILD_TIME=""
ENV BLOOBCAT_BUILD_TIME=${BUILD_TIME}

COPY requirements.txt ./
RUN pip install --no-cache-dir --upgrade -r requirements.txt

COPY . .

ENTRYPOINT [ "python", "-m", "bloobcat" ]
