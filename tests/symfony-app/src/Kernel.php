<?php

namespace App;

use App\Controller\ProfilerTargetController;
use Symfony\Bundle\FrameworkBundle\FrameworkBundle;
use Symfony\Bundle\FrameworkBundle\Kernel\MicroKernelTrait;
use Symfony\Bundle\TwigBundle\TwigBundle;
use Symfony\Bundle\WebProfilerBundle\WebProfilerBundle;
use Symfony\Component\Config\Loader\LoaderInterface;
use Symfony\Component\DependencyInjection\ContainerBuilder;
use Symfony\Component\HttpKernel\Kernel as BaseKernel;
use Symfony\Component\Routing\Loader\Configurator\RoutingConfigurator;

final class Kernel extends BaseKernel
{
    use MicroKernelTrait;

    public function registerBundles(): iterable
    {
        yield new FrameworkBundle();
        yield new TwigBundle();

        if ($this->getEnvironment() === 'dev') {
            yield new WebProfilerBundle();
        }
    }

    protected function configureContainer(
        ContainerBuilder $container,
        LoaderInterface $loader,
    ): void {
        $container->loadFromExtension('framework', [
            'secret' => 'cdpx-profiler-fixture',
            'profiler' => [
                'enabled' => true,
                'collect' => true,
            ],
            'router' => [
                'utf8' => true,
            ],
        ]);

        $container->loadFromExtension('web_profiler', [
            'toolbar' => false,
            'intercept_redirects' => false,
        ]);
    }

    protected function configureRoutes(RoutingConfigurator $routes): void
    {
        if ($this->getEnvironment() === 'dev') {
            $profilerRoutes = dirname(__DIR__)
                .'/vendor/symfony/web-profiler-bundle/Resources/config/routing';
            $routes->import($profilerRoutes.'/wdt.php')->prefix('/_wdt');
            $routes
                ->import($profilerRoutes.'/profiler.php')
                ->prefix('/_profiler');
        }

        $routes
            ->add('profiler_target', '/profiler-target')
            ->controller([ProfilerTargetController::class, '__invoke']);
    }
}
