<?php declare(strict_types=1);

namespace CdpxE2E;

use CdpxE2E\DependencyInjection\CompilerPass\FeatureFlagCompilerPass;
use Shopware\Core\Framework\Plugin;
use Symfony\Component\DependencyInjection\ContainerBuilder;
use Symfony\Component\DependencyInjection\Compiler\PassConfig;

final class CdpxE2E extends Plugin
{
    public function build(ContainerBuilder $container): void
    {
        parent::build($container);

        // Shopware registers its FeatureFlagCompilerPass at priority 1000.
        // The deterministic E2E flag must exist before that native pass reads
        // and registers the shopware.feature.flags parameter.
        $container->addCompilerPass(
            new FeatureFlagCompilerPass(),
            PassConfig::TYPE_BEFORE_OPTIMIZATION,
            2000,
        );
    }
}
