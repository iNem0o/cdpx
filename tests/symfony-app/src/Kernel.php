<?php

namespace App;

use App\Controller\ApiController;
use App\Controller\ProfilerTargetController;
use App\Controller\ScenarioController;
use App\Message\QueuedPing;
use App\MessageHandler\SyncPingHandler;
use App\Scenario\DatabaseSeeder;
use Doctrine\Bundle\DoctrineBundle\DoctrineBundle;
use Symfony\Bundle\FrameworkBundle\FrameworkBundle;
use Symfony\Bundle\FrameworkBundle\Kernel\MicroKernelTrait;
use Symfony\Bundle\TwigBundle\TwigBundle;
use Symfony\Bundle\WebProfilerBundle\WebProfilerBundle;
use Symfony\Component\Config\Loader\LoaderInterface;
use Symfony\Component\DependencyInjection\ContainerBuilder;
use Symfony\Component\DependencyInjection\Reference;
use Symfony\Component\HttpKernel\Kernel as BaseKernel;
use Symfony\Component\Routing\Loader\Configurator\RoutingConfigurator;

final class Kernel extends BaseKernel
{
    use MicroKernelTrait;

    public function registerBundles(): iterable
    {
        yield new FrameworkBundle();
        yield new TwigBundle();
        yield new DoctrineBundle();

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
            'http_client' => [
                'default_options' => [
                    'timeout' => 5,
                ],
            ],
            'messenger' => [
                'transports' => [
                    'queue' => 'in-memory://',
                ],
                'routing' => [
                    // SyncPing n'est PAS routé: traité en direct par son
                    // handler -> une seule entrée dans le collector (le
                    // transport sync:// ferait collecter dispatch + réception).
                    QueuedPing::class => 'queue',
                ],
            ],
            'cache' => [
                'pools' => [
                    // Adapter array = compteurs intra-requête 100 % déterministes.
                    'app.scenario_pool' => ['adapter' => 'cache.adapter.array'],
                ],
            ],
        ]);

        $container->loadFromExtension('web_profiler', [
            'toolbar' => false,
            'intercept_redirects' => false,
        ]);

        $container->loadFromExtension('doctrine', [
            'dbal' => [
                'default_connection' => 'default',
                'connections' => [
                    'default' => [
                        'url' => 'sqlite:///%kernel.project_dir%/var/data.db',
                        'profiling_collect_backtrace' => false,
                    ],
                    // Le seeding passe par cette connexion non profilée pour ne
                    // JAMAIS polluer les compteurs du panel db des scénarios.
                    'seed' => [
                        'url' => 'sqlite:///%kernel.project_dir%/var/data.db',
                        'profiling' => false,
                        'logging' => false,
                    ],
                ],
            ],
            'orm' => [
                'auto_generate_proxy_classes' => true,
                'naming_strategy' => 'doctrine.orm.naming_strategy.underscore_number_aware',
                'mappings' => [
                    'App' => [
                        'type' => 'attribute',
                        'is_bundle' => false,
                        'dir' => '%kernel.project_dir%/src/Entity',
                        'prefix' => 'App\Entity',
                        'alias' => 'App',
                    ],
                ],
            ],
        ]);

        $container->register(DatabaseSeeder::class)
            ->setAutowired(true)
            ->setPublic(true)
            ->setArgument('$connection', new Reference('doctrine.dbal.seed_connection'));

        $container->register(SyncPingHandler::class)
            ->setAutowired(true)
            ->addTag('messenger.message_handler');

        $container->register(ApiController::class)
            ->setAutowired(true)
            ->setPublic(true)
            ->addTag('controller.service_arguments');

        $container->register(ScenarioController::class)
            ->setAutowired(true)
            ->setPublic(true)
            ->setArgument('$scenarioPool', new Reference('app.scenario_pool'))
            ->addTag('controller.service_arguments');
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

        $routes
            ->add('scenario_profiler', '/scenario/profiler/{case}')
            ->controller([ScenarioController::class, 'profiler']);

        $routes
            ->add('scenario_vitals', '/scenario/vitals/{case}')
            ->controller([ScenarioController::class, 'vitals']);

        $routes
            ->add('scenario_rgaa', '/scenario/rgaa/{case}')
            ->controller([ScenarioController::class, 'rgaa']);

        $routes
            ->add('scenario_front', '/scenario/front/{case}')
            ->controller([ScenarioController::class, 'front']);

        $routes
            ->add('scenario_resource', '/scenario/resource/{kind}/{name}')
            ->controller([ScenarioController::class, 'resource']);

        $routes
            ->add('api_echo', '/api/echo')
            ->controller([ApiController::class, 'echoJson']);

        $routes
            ->add('api_status', '/api/status/{code}')
            ->controller([ApiController::class, 'status'])
            ->requirements(['code' => '\d+']);

        $routes
            ->add('api_slow', '/api/slow')
            ->controller([ApiController::class, 'slow']);
    }
}
