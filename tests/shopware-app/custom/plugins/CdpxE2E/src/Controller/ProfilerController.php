<?php declare(strict_types=1);

namespace CdpxE2E\Controller;

use Doctrine\DBAL\Connection;
use Shopware\Core\Content\Rule\RuleCollection;
use Shopware\Core\Framework\Context;
use Shopware\Core\Framework\DataAbstractionLayer\EntityRepository;
use Shopware\Core\Framework\DataAbstractionLayer\Search\Criteria;
use Symfony\Component\HttpFoundation\Response;
use Symfony\Component\Routing\Attribute\Route;

final class ProfilerController
{
    /** @param EntityRepository<RuleCollection> $ruleRepository */
    public function __construct(
        private readonly Connection $connection,
        private readonly EntityRepository $ruleRepository,
    ) {
    }

    #[Route(
        path: '/cdpx-profiler',
        name: 'frontend.cdpx.profiler',
        defaults: ['_routeScope' => ['storefront']],
        methods: ['GET'],
    )]
    public function __invoke(): Response
    {
        $criteria = (new Criteria())->setLimit(1)->setTitle('cdpx-shopware-e2e');
        $this->ruleRepository->searchIds($criteria, Context::createDefaultContext());

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
