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

    // LAZY volontaire: le cas doctrine-n-plus-one repose sur l'initialisation
    // paresseuse (1 findAll + 1 requête par auteur distinct).
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
