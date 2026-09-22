# Playwright's official image already has Chromium and every system
# library it needs preinstalled - avoids a long, fragile manual apt-get
# list for browser dependencies.
FROM mcr.microsoft.com/playwright/python:v1.47.0-jammy

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Browsers are already in the base image, but this is a harmless no-op
# safety net in case the requirements.txt Playwright version ever drifts
# from the base image's preinstalled version.
RUN playwright install --with-deps chromium

COPY . .

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
