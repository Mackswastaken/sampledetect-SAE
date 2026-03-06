FROM python:3.11-slim

WORKDIR /app

# ffmpeg needed by audfprint audio_read
RUN apt-get update && apt-get install -y ffmpeg && rm -rf /var/lib/apt/lists/*

# Because Render build context is backend/, requirements.txt is at ./requirements.txt
COPY requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt

# Copy backend app code (everything in backend folder)
COPY . /app

ENV PYTHONUNBUFFERED=1

CMD ["sh", "-c", "uvicorn main:app --host 0.0.0.0 --port ${PORT:-8000}"]