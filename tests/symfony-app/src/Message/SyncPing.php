<?php

namespace App\Message;

final class SyncPing
{
    public function __construct(public readonly string $payload)
    {
    }
}
