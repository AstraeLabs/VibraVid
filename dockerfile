FROM python:3.11-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    build-essential \
    libxml2-dev \
    libxslt1-dev \
 && apt-get clean \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY GUI/requirements.txt ./GUI/requirements.txt
RUN pip install --no-cache-dir -r GUI/requirements.txt

COPY . .

ENV PYTHONPATH="/app:${PYTHONPATH}"

EXPOSE 8000

CMD ["python", "GUI/manage.py", "runserver", "0.0.0.0:8000"]