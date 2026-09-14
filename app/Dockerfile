FROM python:3.14-slim-bookworm as builder
WORKDIR /code
COPY requirements.txt .
RUN pip install --user --no-cache-dir --upgrade pip setuptools
RUN pip install --user --no-cache-dir -r requirements.txt

FROM python:3.14-slim-bookworm
RUN apt-get update && apt-get upgrade -y && rm -rf /var/lib/apt/lists/*
WORKDIR /code
COPY --from=builder /root/.local /root/.local
ENV PATH=/root/.local/bin:$PATH

COPY main.py .
EXPOSE 8000
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
