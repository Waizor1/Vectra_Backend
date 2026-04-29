FROM python:3.12-slim

WORKDIR /app

ARG BUILD_TIME=""
ENV BLOOBCAT_BUILD_TIME=${BUILD_TIME}

COPY requirements.txt ./
RUN pip install --no-cache-dir --upgrade -r requirements.txt

RUN addgroup --system --gid 10001 bloobcat \
    && adduser --system --uid 10001 --ingroup bloobcat --home /app --no-create-home bloobcat \
    && mkdir -p /app/logs \
    && chown -R bloobcat:bloobcat /app

COPY --chown=bloobcat:bloobcat . .

USER bloobcat

ENTRYPOINT [ "python", "-m", "bloobcat" ]
