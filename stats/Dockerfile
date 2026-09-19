# BCA stats service: stdlib-only Python, no pip needed.
FROM python:3.11-slim-bookworm

ENV PYTHONUNBUFFERED=1

RUN useradd -m -d /data -s /usr/sbin/nologin bca-stats \
    && mkdir -p /data /app && chown -R bca-stats:bca-stats /data /app

COPY stats.py /app/stats.py

VOLUME ["/data"]
EXPOSE 8090
USER bca-stats
WORKDIR /data
HEALTHCHECK --interval=60s --timeout=10s --start-period=30s --retries=3 \
    CMD python3 -c "import urllib.request; urllib.request.urlopen('http://localhost:8090/health', timeout=8).read()" || exit 1
ENTRYPOINT ["python3", "/app/stats.py"]
