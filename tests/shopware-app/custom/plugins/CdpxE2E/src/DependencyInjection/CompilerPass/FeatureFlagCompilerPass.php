<?php declare(strict_types=1);

namespace CdpxE2E\DependencyInjection\CompilerPass;

use Symfony\Component\DependencyInjection\Compiler\CompilerPassInterface;
use Symfony\Component\DependencyInjection\ContainerBuilder;

final class FeatureFlagCompilerPass implements CompilerPassInterface
{
    public const FLAG = 'CDPX_E2E_FEATURE';

    public function process(ContainerBuilder $container): void
    {
        $flags = $container->getParameter('shopware.feature.flags');
        if (!\is_array($flags)) {
            throw new \RuntimeException('shopware.feature.flags must be an array');
        }

        // Prepend it so the public 20-row bound still contains the runtime
        // marker even when Shopware itself registers more than 20 flags.
        $container->setParameter('shopware.feature.flags', [
            self::FLAG => [
                'default' => false,
                'major' => false,
                'active' => true,
                'description' => 'Deterministic cdpx E2E feature flag',
            ],
            ...$flags,
        ]);
    }
}
