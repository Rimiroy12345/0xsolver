FROM python:3.11-slim-bookworm
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
RUN apt-get update && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends file binutils exiftool tshark unzip p7zip-full gdb gcc libc6-dev curl xxd libmagic1 tesseract-ocr && rm -rf /var/lib/apt/lists/*
WORKDIR /opt/solver
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
RUN useradd -u 10001 -m solver && mkdir /data && chown solver:solver /data
COPY app ./app
USER solver
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
