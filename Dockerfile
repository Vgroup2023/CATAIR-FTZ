FROM python:3.12-slim
WORKDIR /app
COPY ftz ./ftz
ENV FTZ_HOST=0.0.0.0 FTZ_DB=/data/ftz.db PYTHONUNBUFFERED=1
RUN useradd -m app && mkdir /data && chown app /data
USER app
VOLUME /data
EXPOSE 8214
HEALTHCHECK CMD python -c "import urllib.request,os;urllib.request.urlopen('http://127.0.0.1:%s/healthz'%os.environ.get('PORT','8214'))"
CMD ["python", "-m", "ftz"]
