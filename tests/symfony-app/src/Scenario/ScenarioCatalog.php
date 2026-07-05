<?php

namespace App\Scenario;

final class ScenarioCatalog
{
    public static function profiler(string $case): array
    {
        $base = [
            'duration_ms' => 18,
            'memory_kb' => 640,
            'db_queries' => 3,
            'db_duplicate_queries' => 0,
            'cache_state' => 'hit',
            'cache_hit' => true,
            'payload_bytes' => 512,
            'twig_renders' => 2,
            'twig_render_ms' => 4,
            'stopwatch_sections' => 1,
            'http_client' => 'none',
            'http_client_ms' => 0,
            'messenger' => 'none',
            'queue_depth' => 0,
            'route_outcome' => 'ok',
            'response_status' => 200,
            'cache_control' => 'private, max-age=0',
            'etag' => 'cdpx-profiler-base',
            'expected' => 'ok',
        ];

        $cases = [
            'baseline' => [
                ...$base,
                'id' => 'profiler.baseline',
                'title' => 'Profiler baseline',
                'duration_ms' => 12,
                'memory_kb' => 512,
                'db_queries' => 2,
                'cache_hit' => false,
                'cache_state' => 'miss',
                'payload_bytes' => 256,
            ],
            'degraded' => [
                ...$base,
                'id' => 'profiler.degraded',
                'title' => 'Profiler degraded',
                'duration_ms' => 42,
                'memory_kb' => 768,
                'db_queries' => 7,
                'db_duplicate_queries' => 2,
                'cache_hit' => false,
                'cache_state' => 'miss',
                'payload_bytes' => 2048,
            ],
            'doctrine-normal' => [
                ...$base,
                'id' => 'profiler.doctrine-normal',
                'title' => 'Doctrine normal query set',
            ],
            'doctrine-n-plus-one' => [
                ...$base,
                'id' => 'profiler.doctrine-n-plus-one',
                'title' => 'Doctrine N+1 query set',
                'duration_ms' => 36,
                'memory_kb' => 700,
                'db_queries' => 9,
                'db_duplicate_queries' => 5,
            ],
            'doctrine-duplicates' => [
                ...$base,
                'id' => 'profiler.doctrine-duplicates',
                'title' => 'Doctrine duplicate query burst',
                'duration_ms' => 44,
                'db_queries' => 12,
                'db_duplicate_queries' => 8,
                'expected' => 'duplicate-query-burst',
            ],
            'cache-miss' => [
                ...$base,
                'id' => 'profiler.cache-miss',
                'title' => 'Cache miss',
                'duration_ms' => 28,
                'memory_kb' => 620,
                'db_queries' => 4,
                'cache_hit' => false,
                'cache_state' => 'miss',
                'payload_bytes' => 768,
            ],
            'cache-hit' => [
                ...$base,
                'id' => 'profiler.cache-hit',
                'title' => 'Cache hit',
                'duration_ms' => 8,
                'memory_kb' => 540,
                'db_queries' => 1,
                'payload_bytes' => 768,
            ],
            'cache-stale' => [
                ...$base,
                'id' => 'profiler.cache-stale',
                'title' => 'Cache stale revalidation',
                'duration_ms' => 23,
                'db_queries' => 2,
                'cache_hit' => false,
                'cache_state' => 'stale',
                'expected' => 'stale-cache-signal',
            ],
            'twig-light' => [
                ...$base,
                'id' => 'profiler.twig-light',
                'title' => 'Twig light render',
                'twig_renders' => 2,
                'twig_render_ms' => 5,
            ],
            'twig-heavy' => [
                ...$base,
                'id' => 'profiler.twig-heavy',
                'title' => 'Twig heavy render',
                'duration_ms' => 58,
                'memory_kb' => 980,
                'twig_renders' => 24,
                'twig_render_ms' => 31,
                'payload_bytes' => 1800,
            ],
            'stopwatch-sections' => [
                ...$base,
                'id' => 'profiler.stopwatch-sections',
                'title' => 'Stopwatch custom sections',
                'duration_ms' => 33,
                'stopwatch_sections' => 4,
                'expected' => 'custom-stopwatch-sections',
            ],
            'http-client-success' => [
                ...$base,
                'id' => 'profiler.http-client-success',
                'title' => 'HTTP client success',
                'duration_ms' => 22,
                'http_client' => 'success',
                'http_client_ms' => 14,
            ],
            'http-client-error' => [
                ...$base,
                'id' => 'profiler.http-client-error',
                'title' => 'HTTP client error',
                'duration_ms' => 29,
                'http_client' => 'error',
                'http_client_ms' => 17,
                'expected' => 'local-http-error',
            ],
            'http-client-timeout' => [
                ...$base,
                'id' => 'profiler.http-client-timeout',
                'title' => 'HTTP client timeout',
                'duration_ms' => 75,
                'http_client' => 'timeout',
                'http_client_ms' => 60,
                'expected' => 'local-http-timeout',
            ],
            'messenger-sync' => [
                ...$base,
                'id' => 'profiler.messenger-sync',
                'title' => 'Messenger sync handling',
                'duration_ms' => 24,
                'messenger' => 'sync-handled',
            ],
            'messenger-queued' => [
                ...$base,
                'id' => 'profiler.messenger-queued',
                'title' => 'Messenger simulated queue signal',
                'duration_ms' => 19,
                'messenger' => 'queued',
                'queue_depth' => 3,
            ],
            'routing-redirect' => [
                ...$base,
                'id' => 'profiler.routing-redirect',
                'title' => 'Routing redirect signal',
                'route_outcome' => 'redirect',
                'response_status' => 302,
                'expected' => 'redirect-detected',
            ],
            'routing-404' => [
                ...$base,
                'id' => 'profiler.routing-404',
                'title' => 'Routing 404 signal',
                'route_outcome' => 'not-found',
                'response_status' => 404,
                'expected' => 'not-found-detected',
            ],
            'routing-500' => [
                ...$base,
                'id' => 'profiler.routing-500',
                'title' => 'Routing 500 exception signal',
                'duration_ms' => 46,
                'route_outcome' => 'exception',
                'response_status' => 500,
                'expected' => 'exception-log-detected',
            ],
            'headers-cache' => [
                ...$base,
                'id' => 'profiler.headers-cache',
                'title' => 'Response cache headers',
                'cache_control' => 'public, max-age=60',
                'etag' => 'cdpx-profiler-cache-v1',
                'expected' => 'cache-headers-present',
            ],
        ];

        return self::get($cases, $case, 'profiler');
    }

    public static function vitals(string $case): array
    {
        $base = [
            'hero_text' => 'Stable Symfony dashboard',
            'layout_shift' => false,
            'interaction_work_ms' => 8,
            'payload_blocks' => 2,
            'lcp_element' => '#hero-title',
            'lcp_type' => 'text',
            'lcp_size' => 1200,
            'resource_profile' => 'balanced',
            'critical_css' => 1,
            'critical_js' => 1,
            'critical_images' => 0,
            'critical_fonts' => 0,
            'shift_count' => 0,
            'max_shift' => 0.0,
            'long_tasks' => 0,
            'network_variant' => 'default',
            'cpu_variant' => 'default',
        ];

        $cases = [
            'baseline' => [
                ...$base,
                'id' => 'vitals.baseline',
                'title' => 'Vitals baseline',
            ],
            'degraded' => [
                ...$base,
                'id' => 'vitals.degraded',
                'title' => 'Vitals degraded',
                'hero_text' => 'Degraded Symfony dashboard',
                'layout_shift' => true,
                'interaction_work_ms' => 70,
                'payload_blocks' => 8,
                'shift_count' => 1,
                'max_shift' => 0.18,
                'long_tasks' => 1,
                'network_variant' => 'slow-3g',
                'cpu_variant' => 'cpu-4x',
            ],
            'lcp-image' => [
                ...$base,
                'id' => 'vitals.lcp-image',
                'title' => 'LCP image candidate',
                'hero_text' => 'Image-led Symfony dashboard',
                'lcp_element' => '#hero-image',
                'lcp_type' => 'image',
                'lcp_size' => 6400,
                'critical_images' => 1,
            ],
            'lcp-text' => [
                ...$base,
                'id' => 'vitals.lcp-text',
                'title' => 'LCP text candidate',
                'hero_text' => 'Large text Symfony dashboard',
                'lcp_element' => '#hero-title',
                'lcp_type' => 'text',
                'lcp_size' => 1800,
            ],
            'cls-injected-banner' => [
                ...$base,
                'id' => 'vitals.cls-injected-banner',
                'title' => 'CLS injected banner',
                'layout_shift' => true,
                'shift_count' => 1,
                'max_shift' => 0.22,
            ],
            'inp-long-task' => [
                ...$base,
                'id' => 'vitals.inp-long-task',
                'title' => 'INP long task',
                'interaction_work_ms' => 95,
                'long_tasks' => 1,
                'cpu_variant' => 'cpu-4x',
            ],
            'resource-blocking' => [
                ...$base,
                'id' => 'vitals.resource-blocking',
                'title' => 'Resource blocking diagnostics',
                'payload_blocks' => 10,
                'resource_profile' => 'blocking',
                'critical_css' => 2,
                'critical_js' => 3,
                'critical_images' => 2,
                'critical_fonts' => 1,
                'network_variant' => 'slow-3g',
            ],
        ];

        return self::get($cases, $case, 'vitals');
    }

    public static function rgaa(string $case): array
    {
        $cases = [
            'baseline' => [
                'id' => 'rgaa.baseline',
                'title' => 'Accessible Symfony form',
                'variant' => 'baseline',
                'automated_scope' => 'automated subset',
            ],
            'regression' => [
                'id' => 'rgaa.regression',
                'title' => 'Accessibility regression',
                'variant' => 'regression',
                'automated_scope' => 'automated subset',
            ],
        ];

        return self::get($cases, $case, 'rgaa');
    }

    public static function front(string $case): array
    {
        $cases = [
            'states' => [
                'id' => 'front.states',
                'title' => 'Front state transition',
                'before' => 'idle',
                'after' => 'submitted',
            ],
        ];

        return self::get($cases, $case, 'front');
    }

    private static function get(array $cases, string $case, string $family): array
    {
        if (!array_key_exists($case, $cases)) {
            throw new \InvalidArgumentException(sprintf('Unknown %s scenario: %s', $family, $case));
        }

        return $cases[$case];
    }
}
