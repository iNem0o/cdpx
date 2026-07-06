<?php

namespace App\Scenario;

final class ScenarioCatalog
{
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
