<?php

namespace App\Scenario;

final class ScenarioCatalog
{
    public static function profiler(string $case): array
    {
        $cases = [
            'baseline' => [
                'id' => 'profiler.baseline',
                'title' => 'Profiler baseline',
                'duration_ms' => 12,
                'memory_kb' => 512,
                'db_queries' => 2,
                'db_duplicate_queries' => 0,
                'cache_hit' => false,
                'payload_bytes' => 256,
            ],
            'degraded' => [
                'id' => 'profiler.degraded',
                'title' => 'Profiler degraded',
                'duration_ms' => 42,
                'memory_kb' => 768,
                'db_queries' => 7,
                'db_duplicate_queries' => 2,
                'cache_hit' => false,
                'payload_bytes' => 2048,
            ],
            'doctrine-normal' => [
                'id' => 'profiler.doctrine-normal',
                'title' => 'Doctrine normal query set',
                'duration_ms' => 18,
                'memory_kb' => 640,
                'db_queries' => 3,
                'db_duplicate_queries' => 0,
                'cache_hit' => true,
                'payload_bytes' => 512,
            ],
            'doctrine-n-plus-one' => [
                'id' => 'profiler.doctrine-n-plus-one',
                'title' => 'Doctrine N+1 query set',
                'duration_ms' => 36,
                'memory_kb' => 700,
                'db_queries' => 9,
                'db_duplicate_queries' => 5,
                'cache_hit' => true,
                'payload_bytes' => 512,
            ],
            'cache-miss' => [
                'id' => 'profiler.cache-miss',
                'title' => 'Cache miss',
                'duration_ms' => 28,
                'memory_kb' => 620,
                'db_queries' => 4,
                'db_duplicate_queries' => 0,
                'cache_hit' => false,
                'payload_bytes' => 768,
            ],
            'cache-hit' => [
                'id' => 'profiler.cache-hit',
                'title' => 'Cache hit',
                'duration_ms' => 8,
                'memory_kb' => 540,
                'db_queries' => 1,
                'db_duplicate_queries' => 0,
                'cache_hit' => true,
                'payload_bytes' => 768,
            ],
        ];

        return self::get($cases, $case, 'profiler');
    }

    public static function vitals(string $case): array
    {
        $cases = [
            'baseline' => [
                'id' => 'vitals.baseline',
                'title' => 'Vitals baseline',
                'hero_text' => 'Stable Symfony dashboard',
                'layout_shift' => false,
                'interaction_work_ms' => 8,
                'payload_blocks' => 2,
            ],
            'degraded' => [
                'id' => 'vitals.degraded',
                'title' => 'Vitals degraded',
                'hero_text' => 'Degraded Symfony dashboard',
                'layout_shift' => true,
                'interaction_work_ms' => 70,
                'payload_blocks' => 8,
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
                'expected' => [
                    'single_h1' => true,
                    'has_main_landmark' => true,
                    'all_inputs_labelled' => true,
                    'focus_visible' => true,
                    'contrast_token' => 'AA',
                ],
            ],
            'regression' => [
                'id' => 'rgaa.regression',
                'title' => 'Accessibility regression',
                'expected' => [
                    'single_h1' => false,
                    'has_main_landmark' => false,
                    'all_inputs_labelled' => false,
                    'focus_visible' => false,
                    'contrast_token' => 'fail',
                ],
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
