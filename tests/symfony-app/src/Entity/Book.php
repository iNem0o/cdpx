<?php

namespace App\Entity;

use Doctrine\ORM\Mapping as ORM;

#[ORM\Entity]
#[ORM\Table(name: 'book')]
class Book
{
    #[ORM\Id]
    #[ORM\Column(type: 'integer')]
    private int $id;

    #[ORM\Column(type: 'string')]
    private string $title;

    // LAZY on purpose: the doctrine-n-plus-one case relies on lazy
    // initialization (1 findAll + 1 query per distinct author).
    #[ORM\ManyToOne(targetEntity: Author::class, fetch: 'LAZY')]
    #[ORM\JoinColumn(name: 'author_id', nullable: false)]
    private Author $author;

    public function getId(): int
    {
        return $this->id;
    }

    public function getTitle(): string
    {
        return $this->title;
    }

    public function getAuthor(): Author
    {
        return $this->author;
    }
}
