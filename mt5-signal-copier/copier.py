"""Copieur de signaux -> MetaTrader 5.

    python copier.py --source console          # tester en collant des signaux à la main
    python copier.py --list-chats              # trouver l'ID de ton canal Telegram
    python copier.py                           # écouter les canaux Telegram de la config
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import sys
import tomllib
from pathlib import Path

from mt5_executor import MT5Executor
from signal_parser import parse_signal

log = logging.getLogger("copier")


class Copier:
    def __init__(self, cfg: dict, executor: MT5Executor):
        self.executor = executor
        self.aliases = cfg.get("aliases", {})
        self.extra_symbols = cfg.get("trading", {}).get("extra_symbols", [])

    def on_message(self, text: str, tag: str, reply_tag: str | None = None) -> None:
        signal = parse_signal(text, self.aliases, self.extra_symbols)
        if signal is None:
            log.debug("Message ignoré [%s] : %r", tag, text[:80])
            return
        try:
            self.executor.handle(signal, tag, reply_tag)
        except Exception:  # un signal mal formé ne doit jamais arrêter le copieur
            log.exception("Erreur pendant le traitement du signal [%s]", tag)


def run_console(copier: Copier) -> None:
    print("Colle un signal puis une ligne vide pour l'envoyer (Ctrl+Z/Ctrl+D pour quitter).\n"
          "Pour simuler une réponse au message n°2, commence par « >2 », ex. : >2 close\n")
    count, lines = 0, []

    def flush() -> None:
        nonlocal count, lines
        if not lines:
            return
        count += 1
        text, reply = "\n".join(lines), None
        if text.startswith(">"):
            ref, _, text = text[1:].partition(" ")
            reply = f"console_{ref}"
        print(f"--- message n°{count}")
        copier.on_message(text, f"console_{count}", reply)
        lines = []

    for line in sys.stdin:
        if line.strip():
            lines.append(line.rstrip("\n"))
        else:
            flush()
    flush()


def run_telegram(copier: Copier, tcfg: dict, list_chats: bool) -> None:
    from telethon import TelegramClient, events

    channels = tcfg.get("channels") or []
    if not list_chats and not channels:
        sys.exit("Renseigne [telegram].channels dans la config (utilise --list-chats pour les IDs).")

    def tag_for(chat_id: int, msg_id: int) -> str:
        return f"tg{abs(chat_id) % 10**6}_{msg_id}"

    async def main() -> None:
        client = TelegramClient(tcfg.get("session", "copier"), int(tcfg["api_id"]), tcfg["api_hash"])
        await client.start()  # 1er lancement : demande ton numéro et le code reçu sur Telegram
        if list_chats:
            async for dialog in client.iter_dialogs():
                print(f"{dialog.id:>16}  {dialog.name}")
            await client.disconnect()
            return

        @client.on(events.NewMessage(chats=channels))
        async def handler(event) -> None:
            msg = event.message
            reply = tag_for(event.chat_id, msg.reply_to_msg_id) if msg.reply_to_msg_id else None
            copier.on_message(msg.message or "", tag_for(event.chat_id, msg.id), reply)

        async def manage_loop() -> None:
            while True:
                try:
                    copier.executor.manage()
                except Exception:
                    log.exception("Erreur pendant le suivi des positions")
                await asyncio.sleep(interval)

        interval = float(tcfg.get("manage_interval_seconds", 5))
        manager = asyncio.create_task(manage_loop())  # noqa: F841 (garde une référence)
        log.info("En écoute sur %s", ", ".join(map(str, channels)))
        await client.run_until_disconnected()

    asyncio.run(main())


def main() -> None:
    parser = argparse.ArgumentParser(description="Copie automatique de signaux vers MT5")
    parser.add_argument("-c", "--config", default="config.toml")
    parser.add_argument("--source", choices=("telegram", "console"), default="telegram")
    parser.add_argument("--list-chats", action="store_true", help="affiche tes canaux Telegram et leurs IDs")
    parser.add_argument("--dry-run", action="store_true", help="force la simulation, quelle que soit la config")
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args()

    if not Path(args.config).exists():
        sys.exit(f"{args.config} introuvable : copie config.example.toml en config.toml et remplis-le.")
    with open(args.config, "rb") as f:
        cfg = tomllib.load(f)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)-7s %(message)s",
        handlers=[logging.StreamHandler(), logging.FileHandler("copier.log", encoding="utf-8")],
    )

    trading = cfg.get("trading", {})
    if args.dry_run:
        trading["dry_run"] = True
    executor = MT5Executor(cfg.get("mt5", {}), trading, cfg.get("symbol_map", {}))
    if not args.list_chats:
        executor.connect()
    copier = Copier(cfg, executor)
    try:
        if args.source == "console" and not args.list_chats:
            run_console(copier)
        else:
            run_telegram(copier, cfg.get("telegram", {}), args.list_chats)
    except KeyboardInterrupt:
        pass
    finally:
        executor.shutdown()


if __name__ == "__main__":
    main()
