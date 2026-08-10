# syntax=docker/dockerfile:1.7

ARG PYTHON_VERSION=3.11
ARG APP_USER=app
ARG APP_UID=10001

FROM python:${PYTHON_VERSION}-slim AS builder

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /build

RUN apt-get update \
 && apt-get install -y --no-install-recommends \
        build-essential \
        libpangoft2-1.0-0 \
        libpangocairo-1.0-0 \
        libcairo2 \
        libgdk-pixbuf-2.0-0 \
        libffi-dev \
        shared-mime-info \
        fonts-liberation \
 && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml README.md ./
COPY app ./app

RUN pip install --upgrade pip \
 && pip wheel --no-deps --wheel-dir /wheels . \
 && pip download --dest /wheels --no-deps --no-binary=:none: . || true \
 && pip install --prefix=/install .

FROM python:${PYTHON_VERSION}-slim AS runtime

ARG APP_USER
ARG APP_UID

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV PYTHONPATH="/install/lib/python3.11/site-packages"
ENV PATH="/install/bin:/usr/local/bin:/usr/local/sbin:/usr/sbin:/usr/bin:/sbin:/bin"

# Driver ODBC da Microsoft (msodbcsql18) + unixODBC, exigidos pelo pyodbc para
# falar com o SQL Server do Sisloc. Ver docs/exploracao/sql-server-driver.md.
# curl/gnupg são temporários (só para registrar o repo da Microsoft) e saem no
# mesmo RUN, para não engordar a camada. Custo líquido: ~8-11 MB.
RUN apt-get update \
 && apt-get install -y --no-install-recommends \
        libpangoft2-1.0-0 \
        libpangocairo-1.0-0 \
        libcairo2 \
        libgdk-pixbuf-2.0-0 \
        libffi8 \
        shared-mime-info \
        fonts-liberation \
        curl ca-certificates gnupg \
 && DEBIAN_VERSION="$(grep VERSION_ID /etc/os-release | cut -d '"' -f 2 | cut -d '.' -f 1)" \
 && curl -sSL -o /tmp/packages-microsoft-prod.deb \
        "https://packages.microsoft.com/config/debian/${DEBIAN_VERSION}/packages-microsoft-prod.deb" \
 && dpkg -i /tmp/packages-microsoft-prod.deb \
 && rm -f /tmp/packages-microsoft-prod.deb \
 && apt-get update \
 && ACCEPT_EULA=Y apt-get install -y --no-install-recommends \
        msodbcsql18 \
        unixodbc \
        libgssapi-krb5-2 \
 && apt-get purge -y --auto-remove curl gnupg \
 && rm -rf /var/lib/apt/lists/*

RUN groupadd --system --gid ${APP_UID} ${APP_USER} \
 && useradd --system --uid ${APP_UID} --gid ${APP_USER} --no-create-home --shell /usr/sbin/nologin ${APP_USER}

WORKDIR /app

COPY --from=builder /install /install
COPY app ./app
COPY alembic.ini ./alembic.ini
COPY alembic ./alembic

USER ${APP_USER}

EXPOSE 8000

HEALTHCHECK --interval=10s --timeout=3s --start-period=5s --retries=3 \
    CMD python -c "import urllib.request,sys; \
        sys.exit(0) if urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=2).status==200 else sys.exit(1)" || exit 1

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]

FROM runtime AS dev

USER root

# sqlcmd: transforma "diagnosticar rota até o Sisloc" em uma linha, em vez de
# ler stacktrace de Python. Só no estágio dev.
RUN apt-get update \
 && ACCEPT_EULA=Y apt-get install -y --no-install-recommends mssql-tools18 \
 && rm -rf /var/lib/apt/lists/*
ENV PATH="/opt/mssql-tools18/bin:/install/bin:/usr/local/bin:/usr/local/sbin:/usr/sbin:/usr/bin:/sbin:/bin"

RUN pip install --prefix=/install --no-cache-dir --root-user-action=ignore \
        "pytest==8.3.4" \
        "pytest-asyncio==0.25.0" \
        "pytest-cov==6.0.0" \
        "ruff==0.8.4" \
        "mypy==1.13.0" \
        "types-PyYAML==6.0.12.20240917" \
        "types-Markdown==3.7.0.20241204"

USER ${APP_USER}
