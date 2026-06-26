FROM node:22-alpine

RUN apk add --no-cache nginx

WORKDIR /app

COPY backend/package.json /app/backend/package.json
RUN cd /app/backend && npm install --omit=dev

COPY backend/server.js /app/backend/server.js
COPY application/nginx.conf /etc/nginx/nginx.conf
COPY application/conf.d /etc/nginx/conf.d
COPY application/start.sh /app/start.sh

RUN chmod +x /app/start.sh \
    && mkdir -p /run/nginx /var/log/application

CMD ["/app/start.sh"]
