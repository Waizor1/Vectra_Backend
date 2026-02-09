FROM python:3.12-slim AS builder

WORKDIR /tmp

RUN pip install poetry
COPY pyproject.toml poetry.lock /tmp/

RUN pip install poetry-plugin-export
RUN poetry lock
RUN poetry export -f requirements.txt --output requirements.txt --without-hashes

FROM python:3.12-slim

WORKDIR /app

ARG BUILD_TIME=""
ENV BLOOBCAT_BUILD_TIME=${BUILD_TIME}

COPY --from=builder /tmp/requirements.txt ./
RUN pip install --no-cache-dir --upgrade -r requirements.txt

COPY . .

ENTRYPOINT [ "python", "-m", "bloobcat" ]
