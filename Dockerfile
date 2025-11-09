FROM python:3.13 AS python-base

# https://python-poetry.org/docs#ci-recommendations
ENV POETRY_VERSION=2.2.1
ENV POETRY_HOME=/opt/poetry
ENV POETRY_VENV=/opt/poetry-venv

# Tell Poetry where to place its cache and virtual environment
ENV POETRY_CACHE_DIR=/opt/.cache

# Create stage for Poetry installation
FROM python-base  AS poetry-base

# Creating a virtual environment just for poetry and install it with pip
RUN python3 -m venv $POETRY_VENV \
	&& $POETRY_VENV/bin/pip install -U pip setuptools \
	&& $POETRY_VENV/bin/pip install poetry==${POETRY_VERSION}

# Create a new stage from the base python image
FROM python-base AS build-app

# Copy Poetry to app image
COPY --from=poetry-base ${POETRY_VENV} ${POETRY_VENV}

# Add Poetry to PATH
ENV PATH="${PATH}:${POETRY_VENV}/bin"

WORKDIR /app

# Copy Dependencies
COPY poetry.lock pyproject.toml ./

# Install Dependencies
RUN poetry config virtualenvs.in-project true \
    && poetry config virtualenvs.options.no-pip true \
    && poetry install --no-interaction --no-cache

RUN ls -la /app

FROM python:3.13-slim AS app_images

ENV POETRY_VENV=/app/.venv
COPY --from=build-app ${POETRY_VENV} ${POETRY_VENV}
ENV PATH="${POETRY_VENV}/bin:${PATH}"

WORKDIR /app
# Copy Application
COPY . /app

## Run Application
CMD ["python", "main.py"]
