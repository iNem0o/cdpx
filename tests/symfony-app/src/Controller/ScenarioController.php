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
        $response = new JsonResponse([
            'ok' => true,
            'scenario' => $scenario,
            'payload_hash' => hash('sha256', $payload),
        ]);

        $headers = [
            'X-CDPX-Scenario' => $scenario['id'],
            'X-CDPX-Profiler-Time-Ms' => (string) $scenario['duration_ms'],
            'X-CDPX-Profiler-Memory-Kb' => (string) $scenario['memory_kb'],
            'X-CDPX-Profiler-Db-Queries' => (string) $scenario['db_queries'],
            'X-CDPX-Profiler-Db-Duplicate-Queries' => (string) $scenario['db_duplicate_queries'],
            'X-CDPX-Profiler-Cache-Hit' => $scenario['cache_hit'] ? '1' : '0',
            'X-CDPX-Profiler-Payload-Bytes' => (string) $scenario['payload_bytes'],
        ];
        foreach ($headers as $name => $value) {
            $response->headers->set($name, $value);
        }

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

        return new Response(
            <<<HTML
<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <title>{$scenario['title']}</title>
  <style>
    body { font-family: system-ui, sans-serif; margin: 0; color: #172026; background: #f7faf9; }
    header { padding: 24px; background: #0f766e; color: white; }
    main { padding: 24px; max-width: 760px; }
    .hero { font-size: 34px; font-weight: 700; margin: 0 0 16px; }
    .metric-line { min-height: 20px; margin: 8px 0; }
    .late { height: 0; overflow: hidden; transition: none; }
    .late[data-shift="true"] { height: 80px; background: #facc15; }
    button { min-height: 44px; padding: 10px 16px; }
  </style>
</head>
<body data-scenario="{$scenario['id']}">
  <header><div class="hero">{$scenario['hero_text']}</div></header>
  <main>
    <button id="inp-button" type="button">Run interaction</button>
    <div id="state">idle</div>
    <div id="late-slot" class="late" data-shift="false"></div>
    {$blocks}
  </main>
  <script>
    window.__scenarioExpected = {$scenarioJson};
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
        $expected = $this->json($scenario['expected']);
        $contrastToken = $scenario['expected']['contrast_token'];
        if ($case === 'baseline') {
            $focusVisible = 'true';
            $body = <<<HTML
<header><nav aria-label="Primary"><a href="#content">Content</a></nav></header>
<main id="content">
  <h1>{$scenario['title']}</h1>
  <form>
    <label for="email">Email</label>
    <input id="email" name="email" type="email">
    <button type="submit">Save</button>
  </form>
</main>
HTML;
            $focus = 'button:focus, input:focus { outline: 3px solid #0f766e; outline-offset: 2px; }';
        } else {
            $focusVisible = 'false';
            $body = <<<HTML
<section id="content">
  <h1>{$scenario['title']}</h1>
  <h1>Duplicate heading</h1>
  <form>
    <input id="email" name="email" type="email" placeholder="Email">
    <button type="submit">Save</button>
  </form>
</section>
HTML;
            $focus = 'button:focus, input:focus { outline: none; }';
        }

        return new Response(
            <<<HTML
<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <title>{$scenario['title']}</title>
  <style>
    body { font-family: system-ui, sans-serif; color: #14213d; background: #ffffff; }
    a, button { color: #0f766e; }
    {$focus}
  </style>
</head>
<body data-scenario="{$scenario['id']}" data-contrast-token="{$contrastToken}" data-focus-visible="{$focusVisible}">
  {$body}
  <script>window.__scenarioExpected = {$expected};</script>
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

    private function json(array $payload): string
    {
        return json_encode($payload, JSON_THROW_ON_ERROR | JSON_UNESCAPED_SLASHES);
    }
}
