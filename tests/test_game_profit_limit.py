"""Per-round profit limits exercise real SQLite balances and game settlement.

Run with ``.venv/bin/python -m unittest discover -s tests -p test_game_profit_limit.py``.
"""

from __future__ import annotations

import asyncio
import inspect
import sys
import unittest
from collections.abc import Mapping
from contextlib import ExitStack
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import nonebot
from nonebot.adapters.onebot.v11 import Message
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

try:
    nonebot.get_driver()
except ValueError:
    nonebot.init()

from nextbot.db import Base, User
from nextbot.plugins import dice, economy, guess_number


class GameProfitLimitTests(unittest.TestCase):
    def _play(
        self,
        game: str,
        limit: int | None,
        *,
        outcome: str = "win",
        initial: int = 2000,
        rounds: int = 1,
    ) -> tuple[dict[str, int], Mapping[str, object]]:
        module = dice if game == "dice" else guess_number
        handler = inspect.unwrap(
            dice.handle_dice if game == "dice" else guess_number.handle_guess_number
        )
        page_name = "create_dice_page" if game == "dice" else "create_guess_number_page"
        params = {
            "cooldown_seconds": 0,
            "triple_multiplier": 1 if outcome == "tie" else 10,
            "exact_multiplier": 1 if outcome == "tie" else 10,
        }
        if limit is not None:
            params["max_profit"] = limit
        engine = create_engine("sqlite://")
        Base.metadata.tables["user"].create(engine)
        sessions = sessionmaker(bind=engine)
        with sessions() as session:
            session.add(User(user_id="10001", name="Player", coins=initial))
            session.commit()
        page = Mock(return_value="http://127.0.0.1/render/test")
        bot = SimpleNamespace(send=AsyncMock())
        event = SimpleNamespace(get_user_id=lambda: "10001")
        args = Message("豹子 100" if game == "dice" else "50 100")
        # Fix only randomness and external I/O; payout and SQL updates remain real.
        rolls = [1, 2, 3] if outcome == "loss" else [6, 6, 6]
        answer = 90 if outcome == "loss" else 70 if outcome == "partial" else 50
        try:
            with ExitStack() as stack:
                stack.enter_context(patch.object(module, "get_session", sessions))
                stack.enter_context(
                    patch.object(
                        module,
                        "get_current_param",
                        side_effect=lambda key, default: params.get(key, default),
                    )
                )
                stack.enter_context(patch.object(module, "_cooldown_map", {}))
                stack.enter_context(patch.object(module, page_name, page))
                stack.enter_context(
                    patch.object(
                        module,
                        "render_and_send_screenshot",
                        AsyncMock(return_value=True),
                    )
                )
                stack.enter_context(
                    patch.object(
                        module.random,
                        "randint",
                        side_effect=rolls * rounds
                        if game == "dice"
                        else [answer] * rounds,
                    )
                )
                for _ in range(rounds):
                    asyncio.run(handler(bot, event, args))
            self.assertEqual(
                bot.send.await_count, 0, "Gameplay unexpectedly rejected or failed"
            )
            with sessions() as session:
                user = session.query(User).filter_by(user_id="10001").one()
                state = {"coins": user.coins}
                for field in ("total_gain", "total_loss", "total_count", "win_count"):
                    prefix = "dice" if game == "dice" else "guess"
                    state[field] = getattr(user, f"{prefix}_{field}")
            return state, page.call_args.kwargs
        finally:
            engine.dispose()

    def test_profit_cap_excludes_stake_and_keeps_statistics_consistent(self) -> None:
        for game in ("dice", "guess"):
            for limit, profit in (
                (500, 500),
                (1, 1),
                (900, 900),
                (1000, 900),
                (0, 900),
                (None, 900),
            ):
                with self.subTest(game=game, limit=limit):
                    state, result = self._play(game, limit)
                    self.assertEqual(
                        state,
                        {
                            "coins": 2000 + profit,
                            "total_gain": profit,
                            "total_loss": 0,
                            "total_count": 1,
                            "win_count": 1,
                        },
                    )
                    self.assertEqual(result["payout"], 100 + profit)
                    self.assertEqual(result["applied_payout"], 100 + profit)
                    self.assertEqual(result["applied_net"], profit)
                    self.assertEqual(result["final_coins"], state["coins"])

    def test_limit_resets_each_round_instead_of_limiting_lifetime_income(self) -> None:
        for game in ("dice", "guess"):
            with self.subTest(game=game):
                state, _ = self._play(game, 500, rounds=2)
                self.assertEqual(
                    state,
                    {
                        "coins": 3000,
                        "total_gain": 1000,
                        "total_loss": 0,
                        "total_count": 2,
                        "win_count": 2,
                    },
                )

    def test_losing_and_break_even_rounds_are_unchanged(self) -> None:
        for game in ("dice", "guess"):
            for outcome, loss in (("loss", 100), ("tie", 0)):
                with self.subTest(game=game, outcome=outcome):
                    state, result = self._play(game, 1, outcome=outcome)
                    self.assertEqual(
                        state,
                        {
                            "coins": 2000 - loss,
                            "total_gain": 0,
                            "total_loss": loss,
                            "total_count": 1,
                            "win_count": 0,
                        },
                    )
                    self.assertEqual(result["applied_payout"], 100 - loss)
        state, result = self._play("guess", 1, outcome="partial")
        self.assertEqual(state["coins"], 1950)
        self.assertEqual(state["total_loss"], 50)
        self.assertEqual(result["applied_payout"], 50)

    def test_global_balance_cap_still_takes_precedence(self) -> None:
        for game in ("dice", "guess"):
            with self.subTest(game=game):
                state, result = self._play(
                    game, 500, initial=economy.MAX_COINS_AMOUNT - 20
                )
                self.assertEqual(state["coins"], economy.MAX_COINS_AMOUNT)
                self.assertEqual(state["total_gain"], 20)
                self.assertEqual(result["payout"], 600)
                self.assertEqual(result["applied_payout"], 120)
                self.assertEqual(result["applied_net"], 20)
                self.assertTrue(result["capped"])


if __name__ == "__main__":
    unittest.main()
