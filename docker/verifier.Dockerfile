FROM nvidia/cuda:13.0.0-runtime-ubuntu24.04
RUN apt-get update && apt-get install -y --no-install-recommends python3 python3-pip && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY requirements/cuda130.lock /tmp/cuda130.lock
COPY requirements/verifier.lock /tmp/verifier.lock
RUN pip3 install --break-system-packages --no-cache-dir -r /tmp/cuda130.lock \
    && pip3 install --break-system-packages --no-cache-dir -r /tmp/verifier.lock
COPY . /app
CMD ["uvicorn", "api.verifier_worker:create_app", "--factory", "--host", "0.0.0.0", "--port", "8010"]
