FROM python:3.11.15-slim-bookworm

ENV DEBIAN_FRONTEND=noninteractive
ENV PYTHONUNBUFFERED=1

WORKDIR /app

RUN apt-get update \
  && apt-get install -y --no-install-recommends \
    ca-certificates \
    ffmpeg \
    fonts-dejavu-core \
  && rm -rf /var/lib/apt/lists/*

COPY requirements-app.txt pyproject.toml README.md ./
RUN pip install --no-cache-dir -r requirements-app.txt
COPY src ./src
RUN pip install --no-cache-dir --no-deps -e .

COPY configs ./configs
COPY schemas ./schemas

CMD ["uvicorn", "video_agent.web.app:app", "--host", "0.0.0.0", "--port", "8000"]
