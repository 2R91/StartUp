# MT5 Signal Copier

Lit tes signaux de copy trading sur **Telegram**, les comprend et passe automatiquement
les ordres sur **MetaTrader 5** : entrée, SL, plusieurs TP, clôture et break-even.

```
Canal Telegram ──► copier.py ──► signal_parser.py ──► mt5_executor.py ──► Terminal MT5 ──► Broker
   (Telethon)                    (texte → Signal)     (lot, contrôles)    (package Python officiel)
```

## Prérequis

- **Windows** (ou un VPS Windows pour tourner 24 h/24) : le package `MetaTrader5` n'existe que sous Windows.
- Le terminal **MetaTrader 5** installé, connecté à ton compte, avec le bouton **Algo Trading** activé.
- **Python 3.11+**.
- Des clés API Telegram (`api_id`, `api_hash`) : https://my.telegram.org → *API development tools*.

## Installation

```bat
cd mt5-signal-copier
pip install -r requirements.txt
copy config.example.toml config.toml
```

Remplis ensuite `config.toml` : identifiants MT5 (ou `login = 0` pour utiliser le compte ouvert
dans le terminal), clés Telegram, canaux à écouter, gestion du risque.

## Utilisation, étape par étape

1. **Teste le parseur à la main** (aucun ordre, fonctionne aussi sur Mac/Linux) :
   ```bat
   python copier.py --source console
   ```
   Colle un signal, puis une ligne vide. Pour simuler une réponse au message n°1, tape `>1 close`.
2. **Trouve l'ID de ton canal** : `python copier.py --list-chats`. Au premier lancement, Telegram
   demande ton numéro et un code (session sauvegardée ensuite dans `copier.session`).
3. **Simulation sur Telegram** avec `dry_run = true` : chaque ordre est vérifié par le broker
   (`order_check`) sans être envoyé. Regarde `copier.log`.
4. **Compte démo** avec `dry_run = false` pendant quelques jours.
5. Seulement ensuite, compte réel.

## Signaux reconnus

| Message | Action |
|---|---|
| `BUY XAUUSD 2650 SL 2640 TP 2660 TP 2670` | achat au marché, 2 positions (une par TP) |
| `🟢 GOLD BUY NOW @ 2650` / `SL: 2640` / `TP1: 2660` | idem, sur plusieurs lignes, emojis ignorés |
| `EURUSD SELL LIMIT 1.0850-1.0860 Stop Loss 1.0890 Take Profit 1.0800` | ordre limite au milieu de la fourchette |
| `GBPJPY buy stop 191.20 stop: 190.50 target 192.40` | ordre stop |
| `ACHAT US30 42000 SL 41900 Objectif 42150` | le français est reconnu aussi |
| `close` / `close half` **en réponse** au signal | clôture (totale ou 50 %) des positions de CE signal |
| `CLOSE XAUUSD` / `CLOSE ALL` | clôture par symbole / tout ce qu'a ouvert le copieur |
| `SL to BE`, `breakeven`, `move SL to entry` | stop ramené au prix d'entrée |

Les messages sans intérêt (`TP1 hit 🔥`, `Good morning`…) sont ignorés. Surnoms gérés : GOLD, SILVER,
NASDAQ/US100, DOW/US30, SPX/US500, DAX/GER40, WTI/OIL, BTC… Tu peux en ajouter dans `[aliases]`.

## Gestion automatique après l'entrée

Pour un canal qui demande « À TP1 : Half + BE » (comme `🔴 SELL NASDAQ / PE : 26850 / SL : 26960 / TP1…TP3`) :

- `tp_split = [0.5, 0.25, 0.25]` : 50 % du lot sort à TP1, 25 % à TP2, 25 % à TP3.
- `breakeven_after_tp1 = true` : dès que la position TP1 est clôturée **par son TP**, le SL des positions
  restantes passe au prix d'entrée (vérifié toutes les `manage_interval_seconds`).
- `shift_levels_to_broker = true` : si le canal annonce 26850 et que ton broker cote 26860, SL et TP sont
  décalés de +10. Les distances (et donc le risque) restent celles du signal.

Pense à `[symbol_map] NAS100 = "..."` avec le nom exact chez ton broker (USTEC, US100_*, NAS100.cash…).

## Sécurités intégrées

- `dry_run = true` par défaut.
- Seules les positions portant le `magic` du copieur sont gérées : tes trades manuels ne sont jamais touchés.
- Signal refusé s'il n'a pas de SL (`require_sl`), si SL/TP sont du mauvais côté du prix, si le symbole
  n'est pas autorisé, ou si le prix s'est trop éloigné de l'entrée (`max_entry_distance_points`).
- Taille de lot : fixe, ou calculée pour risquer `risk_percent` % du solde jusqu'au SL, plafonnée par `max_lot`.
- `max_open_positions`, slippage maximum (`deviation_points`).
- Une clôture sans symbole, sans `ALL` et qui ne répond à aucun signal est ignorée (trop ambiguë).

## Adapter à ton broker

Si ton broker nomme l'or `XAUUSD.m` ou `GOLD`, utilise `symbol_suffix = ".m"` ou
`[symbol_map] XAUUSD = "GOLD"`. Le nom exact apparaît dans la fenêtre *Market Watch* de MT5.

Pour les contrats à échéance comme `US100_Z6` (Z = décembre, 6 = 2026), écris `NAS100 = "US100_*"` :
le copieur ouvre toujours sur le contrat le plus proche encore tradable et passe tout seul au suivant
(`US100_H7`…). « CLOSE NASDAQ » ferme aussi les positions restées sur l'ancien contrat.

## Limites connues

- Les TP/SL exprimés en **pips** (« TP 50 pips ») ne sont pas convertis : le signal est alors refusé.
- Les messages **modifiés** après publication ne sont pas relus, seuls les nouveaux messages le sont.
- Les virgules décimales (`2650,5`) ne sont pas gérées : seul le point fonctionne (`2650.5`).

Si ton canal utilise un autre format, ajoute un exemple dans `tests/test_parser.py` et adapte
`signal_parser.py`. Tests : `python -m unittest discover -s tests`.

## Avertissement

Ce logiciel est fourni sans garantie. Le trading avec effet de levier comporte un risque élevé de perte
en capital. Teste toujours en démo, et vérifie que la copie de signaux est autorisée par ton broker et
par le fournisseur des signaux.
