FROM node:20-alpine AS builder
WORKDIR /app
COPY package*.json ./
RUN npm ci
COPY . .
RUN npm run build

FROM nginx:alpine
# openssl is used by the entrypoint to bootstrap a temporary HTTPS cert.
RUN apk add --no-cache openssl
COPY --from=builder /app/dist /usr/share/nginx/html
COPY nginx.conf /etc/nginx/conf.d/default.conf
COPY docker-entrypoint.sh /docker-entrypoint.d/40-netpulse-ssl.sh
RUN chmod +x /docker-entrypoint.d/40-netpulse-ssl.sh
EXPOSE 80 443
CMD ["nginx", "-g", "daemon off;"]
