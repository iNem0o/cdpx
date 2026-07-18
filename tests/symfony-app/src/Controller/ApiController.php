<?php

namespace App\Controller;

use Symfony\Component\HttpFoundation\JsonResponse;
use Symfony\Component\HttpFoundation\Request;
use Symfony\Component\HttpFoundation\Response;

/**
 * Real local targets for the http_client collector. Docker stays self-
 * contained: the http-client-* scenarios call the app itself (see
 * Dockerfile, PHP_CLI_SERVER_WORKERS > 1 mandatory to avoid deadlock).
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
