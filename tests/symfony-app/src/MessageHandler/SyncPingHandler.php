<?php

namespace App\MessageHandler;

use App\Message\SyncPing;
use Symfony\Component\Messenger\Attribute\AsMessageHandler;

#[AsMessageHandler]
final class SyncPingHandler
{
    public function __invoke(SyncPing $message): void
    {
        // Handler volontairement vide: seul le trajet dispatch -> handled
        // compte pour le collector messenger.
    }
}
