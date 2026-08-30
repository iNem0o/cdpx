<?php declare(strict_types=1);

namespace CdpxE2E\Cart;

use Shopware\Core\Checkout\Cart\Cart;
use Shopware\Core\Checkout\Cart\CartBehavior;
use Shopware\Core\Checkout\Cart\CartDataCollectorInterface;
use Shopware\Core\Checkout\Cart\LineItem\CartDataCollection;
use Shopware\Core\System\SalesChannel\SalesChannelContext;

final class DeterministicCartCollector implements CartDataCollectorInterface
{
    public function collect(
        CartDataCollection $data,
        Cart $original,
        SalesChannelContext $context,
        CartBehavior $behavior,
    ): void {
        // Intentionally empty: its tagged priority/topology is profiler proof.
    }
}
