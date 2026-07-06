<?php

namespace App\Message;

final class QueuedPing
{
    public function __construct(public readonly string $payload)
    {
    }
}
