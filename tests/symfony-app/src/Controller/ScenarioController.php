<?php

namespace App\Controller;

use App\Scenario\ScenarioCatalog;
use Symfony\Component\HttpFoundation\JsonResponse;
use Symfony\Component\HttpFoundation\Response;

final class ScenarioController
{
    public function profiler(string $case): JsonResponse
    {
        $scenario = ScenarioCatalog::profiler($case);
        $payload = str_repeat('x', $scenario['payload_bytes']);
        $statusCode = $scenario['response_status'] === 302 ? 200 : $scenario['response_status'];
        $response = new JsonResponse(
            [
                'ok' => $statusCode < 500,
                'scenario' => $scenario,
                'payload_hash' => hash('sha256', $payload),
                'simulated_collectors' => [
                    'doctrine',
                    'cache',
                    'twig',
                    'stopwatch',
                    'http_client',
                    'messenger',
                    'routing',
                    'response',
                ],
            ],
            $statusCode,
        );

        $headers = [
            'X-CDPX-Scenario' => $scenario['id'],
            'X-CDPX-Profiler-Time-Ms' => (string) $scenario['duration_ms'],
            'X-CDPX-Profiler-Memory-Kb' => (string) $scenario['memory_kb'],
            'X-CDPX-Profiler-Db-Queries' => (string) $scenario['db_queries'],
            'X-CDPX-Profiler-Db-Duplicate-Queries' => (string) $scenario['db_duplicate_queries'],
            'X-CDPX-Profiler-Cache-Hit' => $scenario['cache_hit'] ? '1' : '0',
            'X-CDPX-Profiler-Cache-State' => $scenario['cache_state'],
            'X-CDPX-Profiler-Payload-Bytes' => (string) $scenario['payload_bytes'],
            'X-CDPX-Profiler-Twig-Renders' => (string) $scenario['twig_renders'],
            'X-CDPX-Profiler-Twig-Render-Ms' => (string) $scenario['twig_render_ms'],
            'X-CDPX-Profiler-Stopwatch-Sections' => (string) $scenario['stopwatch_sections'],
            'X-CDPX-Profiler-Http-Client' => $scenario['http_client'],
            'X-CDPX-Profiler-Http-Client-Ms' => (string) $scenario['http_client_ms'],
            'X-CDPX-Profiler-Messenger' => $scenario['messenger'],
            'X-CDPX-Profiler-Queue-Depth' => (string) $scenario['queue_depth'],
            'X-CDPX-Profiler-Route-Outcome' => $scenario['route_outcome'],
            'X-CDPX-Profiler-Response-Status' => (string) $scenario['response_status'],
            'X-CDPX-Profiler-Expected' => $scenario['expected'],
        ];
        foreach ($headers as $name => $value) {
            $response->headers->set($name, $value);
        }
        $response->headers->set('Cache-Control', $scenario['cache_control']);
        $response->headers->set('ETag', '"'.$scenario['etag'].'"');

        return $response;
    }

    public function vitals(string $case): Response
    {
        $scenario = ScenarioCatalog::vitals($case);
        $scenarioJson = $this->json($scenario);
        $blocks = str_repeat(
            '<p class="metric-line">Symfony metric payload block</p>',
            $scenario['payload_blocks'],
        );
        $shift = $scenario['layout_shift'] ? 'true' : 'false';
        $work = (int) $scenario['interaction_work_ms'];
        $resources = $this->vitalsResources($case, $scenario);
        $hero = $scenario['lcp_type'] === 'image'
            ? '<img id="hero-image" src="/scenario/resource/image/hero.png?case='
                .$case.'" alt="Symfony dashboard preview" width="320" height="180">'
            : '<div id="hero-title" class="hero">'.$scenario['hero_text'].'</div>';

        return new Response(
            <<<HTML
<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{$scenario['title']}</title>
  {$resources['styles']}
  {$resources['preloads']}
  <style>
    body { font-family: system-ui, sans-serif; margin: 0; color: #172026; background: #f7faf9; }
    header { padding: 24px; background: #0f766e; color: white; }
    main { padding: 24px; max-width: 760px; }
    .hero { font-size: 34px; font-weight: 700; margin: 0 0 16px; }
    #hero-image { display: block; max-width: 100%; background: #c7d2fe; }
    .metric-line { min-height: 20px; margin: 8px 0; }
    .late { height: 0; overflow: hidden; transition: none; }
    .late[data-shift="true"] { height: 80px; background: #facc15; color: #172026; padding: 12px; }
    button { min-height: 44px; padding: 10px 16px; }
  </style>
</head>
<body data-scenario="{$scenario['id']}">
  <header>{$hero}</header>
  <main>
    <button id="inp-button" type="button">Run interaction</button>
    <div id="state">idle</div>
    <div id="late-slot" class="late" data-shift="false">Injected banner</div>
    {$blocks}
    {$resources['images']}
  </main>
  {$resources['scripts']}
  <script>
    window.__scenarioExpected = {$scenarioJson};
    window.__cdpxVitalsMeta = {
      thresholds: {
        lcp: {good: 2500, poor: 4000},
        inp: {good: 200, poor: 500},
        cls: {good: 0.1, poor: 0.25}
      },
      lcp: {
        selector: '{$scenario['lcp_element']}',
        type: '{$scenario['lcp_type']}',
        size: {$scenario['lcp_size']}
      },
      cls: {
        expected_shift_count: {$scenario['shift_count']},
        expected_max_shift: {$scenario['max_shift']}
      },
      inp: {
        target: '#inp-button',
        expected_event_duration_ms: {$work},
        expected_long_tasks: {$scenario['long_tasks']}
      },
      emulation: {
        network: '{$scenario['network_variant']}',
        cpu: '{$scenario['cpu_variant']}'
      },
      resource_profile: '{$scenario['resource_profile']}',
      critical_resources: {
        css: {$scenario['critical_css']},
        js: {$scenario['critical_js']},
        images: {$scenario['critical_images']},
        font: {$scenario['critical_fonts']}
      }
    };
    setTimeout(() => {
      document.getElementById('late-slot').dataset.shift = '{$shift}';
    }, 60);
    document.getElementById('inp-button').addEventListener('click', () => {
      const start = performance.now();
      while (performance.now() - start < {$work}) {}
      document.body.dataset.clicked = '1';
      document.getElementById('state').textContent = 'clicked';
    });
  </script>
</body>
</html>
HTML,
            200,
            ['Content-Type' => 'text/html; charset=UTF-8'],
        );
    }

    public function rgaa(string $case): Response
    {
        $scenario = ScenarioCatalog::rgaa($case);
        $scenarioJson = $this->json($scenario);
        $isBaseline = $case === 'baseline';
        $focusVisible = $isBaseline ? 'true' : 'false';
        $contrastToken = $isBaseline ? 'AA' : 'fail';
        $body = $isBaseline ? $this->rgaaBaselineBody($scenario) : $this->rgaaRegressionBody($scenario);
        $focus = $isBaseline
            ? 'a:focus, button:focus, input:focus { outline: 3px solid #0f766e; outline-offset: 2px; }'
            : 'a:focus, button:focus, input:focus { outline: none; }';

        return new Response(
            <<<HTML
<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{$scenario['title']}</title>
  <style>
    body { font-family: system-ui, sans-serif; color: #14213d; background: #ffffff; margin: 0; }
    a, button { color: #0f766e; }
    main, section { padding: 24px; max-width: 760px; }
    table { border-collapse: collapse; margin: 16px 0; }
    th, td { border: 1px solid #94a3b8; padding: 6px 8px; }
    .badge { display: inline-flex; gap: 6px; align-items: center; }
    .status-dot { width: 12px; height: 12px; border-radius: 50%; background: #0f766e; }
    .component { border: 1px solid #64748b; padding: 8px; margin: 10px 0; }
    .clip-check { max-width: 100%; overflow-wrap: anywhere; }
    {$focus}
  </style>
</head>
<body
  data-scenario="{$scenario['id']}"
  data-contrast-token="{$contrastToken}"
  data-focus-visible="{$focusVisible}"
  data-automated-scope="automated subset"
>
  {$body}
  <script>window.__scenarioExpected = {$scenarioJson};</script>
</body>
</html>
HTML,
            200,
            ['Content-Type' => 'text/html; charset=UTF-8'],
        );
    }

    public function front(string $case): Response
    {
        $scenario = ScenarioCatalog::front($case);
        $scenarioJson = $this->json($scenario);

        return new Response(
            <<<HTML
<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <title>{$scenario['title']}</title>
</head>
<body data-state="{$scenario['before']}">
  <main>
    <h1>{$scenario['title']}</h1>
    <button id="submit-btn" type="button">Submit</button>
    <output id="result">{$scenario['before']}</output>
  </main>
  <script>
    window.__scenarioExpected = {$scenarioJson};
    document.getElementById('submit-btn').addEventListener('click', () => {
      document.body.dataset.state = '{$scenario['after']}';
      document.getElementById('result').textContent = '{$scenario['after']}';
    });
  </script>
</body>
</html>
HTML,
            200,
            ['Content-Type' => 'text/html; charset=UTF-8'],
        );
    }

    public function resource(string $kind, string $name): Response
    {
        if ($kind === 'style') {
            return new Response(
                '.metric-line{border-left:2px solid transparent}',
                200,
                ['Content-Type' => 'text/css; charset=UTF-8'],
            );
        }
        if ($kind === 'script') {
            return new Response(
                'window.__cdpxLoadedResources=(window.__cdpxLoadedResources||0)+1;',
                200,
                ['Content-Type' => 'application/javascript; charset=UTF-8'],
            );
        }
        if ($kind === 'font') {
            return new Response(
                'cdpx-font-placeholder',
                200,
                ['Content-Type' => 'font/woff2'],
            );
        }

        $pixel = base64_decode(
            'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+/p9sAAAAASUVORK5CYII=',
            true,
        );

        return new Response($pixel ?: '', 200, ['Content-Type' => 'image/png']);
    }

    private function vitalsResources(string $case, array $scenario): array
    {
        $styles = [];
        $scripts = [];
        $images = [];
        $preloads = [];
        for ($i = 0; $i < $scenario['critical_css']; $i++) {
            $styles[] = '<link rel="stylesheet" href="/scenario/resource/style/main.css?case='
                .$case.'&n='.$i.'">';
        }
        for ($i = 0; $i < $scenario['critical_js']; $i++) {
            $scripts[] = '<script src="/scenario/resource/script/main.js?case='
                .$case.'&n='.$i.'"></script>';
        }
        for ($i = 0; $i < $scenario['critical_images']; $i++) {
            $images[] = '<img src="/scenario/resource/image/probe.png?case='
                .$case.'&n='.$i.'" alt="" width="1" height="1">';
        }
        for ($i = 0; $i < $scenario['critical_fonts']; $i++) {
            $preloads[] = '<link rel="preload" as="font" crossorigin href="/scenario/resource/font/main.woff2?case='
                .$case.'&n='.$i.'">';
        }

        return [
            'styles' => implode("\n  ", $styles),
            'scripts' => implode("\n  ", $scripts),
            'images' => implode("\n    ", $images),
            'preloads' => implode("\n  ", $preloads),
        ];
    }

    private function rgaaBaselineBody(array $scenario): string
    {
        return <<<HTML
<a class="skip-link" href="#content">Skip to content</a>
<header><nav aria-label="Primary"><a href="#content">Content</a></nav></header>
<main id="content">
  <h1>{$scenario['title']}</h1>
  <p class="clip-check">This paragraph can wrap at zoom without clipping or horizontal truncation.</p>
  <img src="/scenario/resource/image/info.png" alt="Quarterly revenue chart">
  <img src="/scenario/resource/image/decorative.png" alt="" role="presentation">
  <a href="/reports"><img src="/scenario/resource/image/report.png" alt="Open reports"></a>
  <iframe title="Embedded help" srcdoc="<p>Help frame</p>"></iframe>
  <p class="badge"><span class="status-dot" aria-hidden="true"></span><span>Status: approved</span></p>
  <video controls><track kind="captions" srclang="en" label="English captions" src="/captions.vtt"></video>
  <p><a href="#transcript">Read transcript</a></p>
  <table>
    <caption>Support requests</caption>
    <thead><tr><th scope="col">Team</th><th scope="col">Open</th></tr></thead>
    <tbody><tr><th scope="row">Core</th><td>4</td></tr></tbody>
  </table>
  <p><a href="/account">Account settings</a></p>
  <button class="component" type="button" aria-pressed="false">Enable alerts</button>
  <form aria-describedby="form-errors">
    <fieldset>
      <legend>Contact details</legend>
      <label for="email">Email</label>
      <input id="email" name="email" type="email" required aria-describedby="email-help">
      <span id="email-help">Use a work email.</span>
    </fieldset>
    <p id="form-errors" role="alert">Email is required.</p>
    <button type="submit">Save</button>
  </form>
</main>
HTML;
    }

    private function rgaaRegressionBody(array $scenario): string
    {
        return <<<HTML
<header><nav><a href="#content">Click here</a></nav></header>
<section id="content">
  <h1>{$scenario['title']}</h1>
  <h1>Duplicate heading</h1>
  <img src="/scenario/resource/image/info.png">
  <img src="/scenario/resource/image/decorative.png" alt="decorative flourish">
  <a href="/reports"><img src="/scenario/resource/image/report.png"></a>
  <iframe srcdoc="<p>Help frame</p>"></iframe>
  <p class="badge"><span class="status-dot"></span></p>
  <video controls></video>
  <table>
    <tr><td>Team</td><td>Open</td></tr>
    <tr><td>Core</td><td>4</td></tr>
  </table>
  <p><a href="/account">Click here</a></p>
  <div class="component" onclick="this.dataset.open='1'">Alerts</div>
  <form>
    <input id="email" name="email" type="email" required>
    <p id="form-errors">Email is required.</p>
    <button type="submit">Save</button>
  </form>
</section>
HTML;
    }

    private function json(array $payload): string
    {
        return json_encode($payload, JSON_THROW_ON_ERROR | JSON_UNESCAPED_SLASHES);
    }
}
