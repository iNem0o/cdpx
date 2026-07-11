# Politique de sécurité

## Versions prises en charge

Les correctifs de sécurité ciblent la dernière version publiée et la branche
par défaut du dépôt. Les versions pré-1.0 plus anciennes peuvent ne pas recevoir
de correctif rétroporté.

## Signaler une vulnérabilité

N'ouvrez pas d'issue publique et ne publiez pas de preuve d'exploitation.
Utilisez le formulaire privé GitHub :

[Signaler une vulnérabilité en privé](https://github.com/inem0o/cdpx/security/advisories/new)

Ce canal est visible uniquement par les mainteneurs autorisés du dépôt. Si le
formulaire n'est pas disponible, n'exposez pas les détails publiquement :
attendez que le propriétaire du dépôt active **Private vulnerability
reporting** dans les paramètres GitHub.

Le rapport devrait contenir, sans données personnelles ni secrets réels :

- la version ou le commit concerné ;
- le scénario de reproduction minimal ;
- l'impact estimé ;
- une proposition de mitigation si elle est connue.

Les mainteneurs qualifient le rapport dans GitHub, coordonnent le correctif et
la divulgation, puis créditent le signalant s'il le souhaite. Aucun délai de
réponse ou programme de récompense n'est garanti.

## Périmètre sensible

cdpx peut exécuter du JavaScript, lire l'état d'une page et piloter des actions
trusted dans le Chrome ciblé. Sont notamment considérés comme sensibles :

- un contournement de `CDPX_ORIGINS` ;
- une fuite de cookies ou d'en-têtes malgré le masquage par défaut ;
- une connexion involontaire à un navigateur non jetable ;
- une exécution de commande système à partir d'une entrée navigateur ;
- une corruption ou une traversée de chemin lors de l'écriture d'artefacts.

Les erreurs d'usage sans impact de sécurité peuvent être signalées dans les
[issues publiques](https://github.com/inem0o/cdpx/issues).
