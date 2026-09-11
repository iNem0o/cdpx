FROM composer:2@sha256:d020706319701a44468968321dccd0fce6620190159a7a9ec195d78e6e971c71

WORKDIR /app
COPY composer.json composer.lock ./
RUN composer install --no-interaction --prefer-dist --no-progress

COPY public ./public
COPY src ./src
COPY templates ./templates
RUN mkdir -p var && chmod -R 777 var

# Important: the http-client-* scenarios call the app itself. A single
# php -S worker would deadlock.
ENV PHP_CLI_SERVER_WORKERS=8

EXPOSE 8000
CMD ["php", "-S", "0.0.0.0:8000", "-t", "public", "public/index.php"]
