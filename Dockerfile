FROM python:3.12-slim

# Set to true to bake Chromium into the image for the browser tool (adds ~400 MB)
ARG INSTALL_BROWSER=false

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PLAYWRIGHT_BROWSERS_PATH=/opt/playwright

WORKDIR /app

COPY requirements.txt .
RUN pip install -r requirements.txt \
    && if [ "$INSTALL_BROWSER" = "true" ]; then python -m playwright install --with-deps chromium; fi

RUN useradd --create-home --uid 10001 clawmind
COPY . .
RUN mkdir -p /app/data /app/workspace && chown clawmind:clawmind /app/data /app/workspace

USER clawmind

# Inside the container the server must listen on all interfaces so the port can be published.
# The app still refuses to start without a password unless DOCKER_LOCAL_ONLY=true is set,
# which docker-compose.yml does because it publishes the port on 127.0.0.1 only.
ENV HOST=0.0.0.0

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=4)"

CMD ["python", "run.py", "--no-install"]
