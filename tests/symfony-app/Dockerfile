FROM composer:2@sha256:5946476338742b200bb9ff88f8be56275ddae4b3949c72305cb0dbf10cfcb760

WORKDIR /app
COPY composer.json composer.lock ./
RUN composer install --no-interaction --prefer-dist --no-progress

COPY public ./public
COPY src ./src
COPY templates ./templates
RUN mkdir -p var && chmod -R 777 var

# CRITIQUE: les scénarios http-client-* appellent l'app elle-même; avec le
# worker unique par défaut de php -S, ce serait un deadlock.
ENV PHP_CLI_SERVER_WORKERS=8

EXPOSE 8000
CMD ["php", "-S", "0.0.0.0:8000", "-t", "public", "public/index.php"]
