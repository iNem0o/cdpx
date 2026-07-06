<?php

namespace App\Scenario;

use Doctrine\DBAL\Connection;

/**
 * Seed SQLite figé: 5 auteurs, 5 livres (1 auteur distinct par livre, pour que
 * le N+1 produise exactement 1 + 5 requêtes). Passe par la connexion `seed`
 * (profiling désactivé) afin que le panel db ne compte QUE les requêtes des
 * scénarios. Idempotent: CREATE IF NOT EXISTS + INSERT OR IGNORE.
 */
final class DatabaseSeeder
{
    private static bool $seeded = false;

    public function __construct(private readonly Connection $connection)
    {
    }

    public function seed(): void
    {
        if (self::$seeded) {
            return;
        }
        $this->connection->executeStatement(
            'CREATE TABLE IF NOT EXISTS author (id INTEGER PRIMARY KEY, name TEXT NOT NULL)'
        );
        $this->connection->executeStatement(
            'CREATE TABLE IF NOT EXISTS book ('
            .'id INTEGER PRIMARY KEY, title TEXT NOT NULL, author_id INTEGER NOT NULL)'
        );
        for ($i = 1; $i <= 5; $i++) {
            $this->connection->executeStatement(
                'INSERT OR IGNORE INTO author (id, name) VALUES (?, ?)',
                [$i, 'Author '.$i],
            );
            $this->connection->executeStatement(
                'INSERT OR IGNORE INTO book (id, title, author_id) VALUES (?, ?, ?)',
                [$i, 'Book '.$i, $i],
            );
        }
        self::$seeded = true;
    }
}
