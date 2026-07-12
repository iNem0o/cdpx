<?php

namespace App\Controller;

use Symfony\Component\HttpFoundation\JsonResponse;
use Symfony\Component\HttpFoundation\Request;
use Symfony\Component\HttpFoundation\Response;

/**
 * Cibles locales réelles du collector http_client. Docker reste autonome:
 * les scénarios http-client-* appellent l'app elle-même (voir Dockerfile,
 * PHP_CLI_SERVER_WORKERS > 1 obligatoire pour éviter le deadlock).
 */
final class ApiController
{
    public function favicon(): Response
    {
        return new Response(null, Response::HTTP_NO_CONTENT);
    }

    public function echoJson(): JsonResponse
    {
        return new JsonResponse(['ok' => true, 'source' => 'api-echo']);
    }

    public function status(int $code): JsonResponse
    {
        return new JsonResponse(['status' => $code], $code);
    }

    public function slow(Request $request): JsonResponse
    {
        $ms = min(500, max(0, $request->query->getInt('ms', 200)));
        usleep($ms * 1000);

        return new JsonResponse(['ok' => true, 'slept_ms' => $ms]);
    }
}
