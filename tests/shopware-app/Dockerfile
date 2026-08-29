FROM ghcr.io/shopware/docker-dev@sha256:7e2087ca05024021be6ef67f62e042e49b8d8e3fcd0d40823521491d789edddc

USER root
WORKDIR /var/www/html
RUN chown www-data:www-data /var/www/html

COPY --chown=www-data:www-data composer.json composer.lock symfony.lock ./
USER www-data
RUN composer install --no-interaction --prefer-dist --no-progress --no-scripts

COPY --chown=www-data:www-data . ./
