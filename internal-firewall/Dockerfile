FROM nginx:1.27-alpine

COPY internal-firewall/nginx.conf /etc/nginx/nginx.conf
COPY internal-firewall/conf.d /etc/nginx/conf.d

RUN mkdir -p /var/log/internal-firewall
