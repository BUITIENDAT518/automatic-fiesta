FROM python:3.12-slim
WORKDIR /app
ENV PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1
COPY pyproject.toml ./
COPY orderhub ./orderhub
RUN pip install .
CMD ["uvicorn", "orderhub.api.main:app", "--host", "0.0.0.0", "--port", "8000"]
