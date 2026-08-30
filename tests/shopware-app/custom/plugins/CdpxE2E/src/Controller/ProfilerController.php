<?php declare(strict_types=1);

namespace CdpxE2E\Controller;

use Doctrine\DBAL\Connection;
use Shopware\Core\Checkout\Cart\LineItem\LineItem;
use Shopware\Core\Checkout\Cart\Price\Struct\QuantityPriceDefinition;
use Shopware\Core\Checkout\Cart\SalesChannel\CartService;
use Shopware\Core\Checkout\Cart\Tax\Struct\TaxRule;
use Shopware\Core\Checkout\Cart\Tax\Struct\TaxRuleCollection;
use Shopware\Core\Content\Rule\RuleCollection;
use Shopware\Core\Framework\Context;
use Shopware\Core\Framework\DataAbstractionLayer\EntityRepository;
use Shopware\Core\Framework\DataAbstractionLayer\Search\Criteria;
use Shopware\Core\System\SalesChannel\SalesChannelContext;
use Symfony\Component\HttpFoundation\Response;
use Symfony\Component\Routing\Attribute\Route;

final class ProfilerController
{
    /** @param EntityRepository<RuleCollection> $ruleRepository */
    public function __construct(
        private readonly Connection $connection,
        private readonly EntityRepository $ruleRepository,
        private readonly CartService $cartService,
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

    #[Route(
        path: '/cdpx-cart-profiler',
        name: 'frontend.cdpx.cart_profiler',
        defaults: ['_routeScope' => ['storefront']],
        methods: ['GET'],
    )]
    public function cart(SalesChannelContext $salesChannelContext): Response
    {
        $cart = $this->cartService->createNew($salesChannelContext->getToken());
        $lineItem = (new LineItem('cdpx-e2e-line', LineItem::CUSTOM_LINE_ITEM_TYPE, null, 2))
            ->setLabel('cdpx deterministic item')
            ->setPayload([
                'cdpx_hidden_dump_canary' => 'CDPX-CART-PAYLOAD-MUST-NOT-LEAK',
            ])
            ->setPriceDefinition(new QuantityPriceDefinition(
                20.0,
                new TaxRuleCollection([new TaxRule(20.0)]),
                2,
            ));
        $this->cartService->add($cart, $lineItem, $salesChannelContext);

        return new Response(
            '<!doctype html><html lang="en"><title>cdpx Shopware Cart profiler</title>'
            . '<body><main id="cdpx-shopware-cart-profiler">Shopware Cart profiler target</main>'
            . '</body></html>'
        );
    }
}
