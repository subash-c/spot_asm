FROM node:20-alpine AS web
WORKDIR /web
COPY frontend/package.json frontend/package-lock.json* ./
RUN npm install
COPY frontend/ ./
RUN npm run build

FROM python:3.12-slim
WORKDIR /app
COPY backend/requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r requirements.txt gunicorn
COPY backend/ /app
COPY --from=web /web/dist /app/frontend_dist
ENV FRONTEND_DIST=/app/frontend_dist
ENV PYTHONUNBUFFERED=1
ENV DEBUG=false
CMD ["sh", "-c", "gunicorn config.wsgi:application --bind 0.0.0.0:${PORT:-8000} --timeout 100 --workers 1 --threads 4"]