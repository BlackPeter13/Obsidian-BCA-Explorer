# Phase-1 monitor: nginx serves the status page and reverse-proxies
# /api/ to the explorer so the browser never hits CORS issues.
FROM nginx:alpine
COPY nginx.conf /etc/nginx/conf.d/default.conf
COPY nbxplorer-monitor.html /usr/share/nginx/html/index.html
EXPOSE 80
