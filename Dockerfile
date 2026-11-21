# Stage 1: Base & Builder dependensi
FROM python:3.11-slim AS base
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Stage 2: Stage Test (Dijalankan oleh Jenkins pada stage 'Test')
FROM base AS test
COPY . .
RUN pytest -v

# Stage 3: Production Runtime Image (Ramping & Aman)
FROM base AS runtime
COPY app.py .
EXPOSE 8000
CMD ["gunicorn", "--bind", "0.0.0.0:8000", "app:app"]