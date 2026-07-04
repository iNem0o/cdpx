<?php

namespace App\Controller;

use Symfony\Component\HttpFoundation\JsonResponse;

final class ProfilerTargetController
{
    public function __invoke(): JsonResponse
    {
        return new JsonResponse([
            'ok' => true,
            'source' => 'symfony-real',
        ]);
    }
}
