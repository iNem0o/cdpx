<?php declare(strict_types=1);

namespace CdpxE2E\Controller;

use Doctrine\DBAL\Connection;
use Symfony\Component\HttpFoundation\Response;
use Symfony\Component\Routing\Attribute\Route;

final class ProfilerController
{
    public function __construct(private readonly Connection $connection)
    {
    }

    #[Route(
        path: '/cdpx-profiler',
        name: 'frontend.cdpx.profiler',
        defaults: ['_routeScope' => ['storefront']],
        methods: ['GET'],
    )]
    public function __invoke(): Response
    {
        for ($index = 0; $index < 5; ++$index) {
            $this->connection->fetchOne('SELECT 1 /* cdpx-shopware-e2e */');
        }

        $middlewares = array_map(
            static fn (object $middleware): string => $middleware::class,
            $this->connection->getConfiguration()->getMiddlewares(),
        );

        return new Response(
            '<!doctype html><html lang="en"><title>cdpx Shopware profiler</title>'
            . '<body><main id="cdpx-shopware-profiler" data-middlewares="'
            . htmlspecialchars(implode(',', $middlewares), ENT_QUOTES)
            . '">Shopware profiler target</main></body></html>'
        );
    }
}
