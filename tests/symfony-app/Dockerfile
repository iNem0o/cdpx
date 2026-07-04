FROM composer:2

WORKDIR /app
COPY composer.json ./
RUN composer install --no-interaction --prefer-dist --no-progress

COPY public ./public
COPY src ./src

EXPOSE 8000
CMD ["php", "-S", "0.0.0.0:8000", "-t", "public", "public/index.php"]
