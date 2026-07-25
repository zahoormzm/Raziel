FROM python:3.11-slim
WORKDIR /app
COPY requirements/core.lock /tmp/core.lock
RUN pip install --no-cache-dir -r /tmp/core.lock
COPY . /app
CMD ["uvicorn", "api.main:create_app", "--factory", "--host", "0.0.0.0", "--port", "8000"]
