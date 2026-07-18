<?php

namespace App\Scenario;

use Doctrine\DBAL\Connection;

/**
 * Frozen SQLite seed: 5 authors, 5 books (1 distinct author per book, so
 * that N+1 produces exactly 1 + 5 queries). Goes through the `seed`
 * connection (profiling disabled) so the db panel counts ONLY the
 * scenarios' queries. Idempotent: CREATE IF NOT EXISTS + INSERT OR IGNORE.
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
